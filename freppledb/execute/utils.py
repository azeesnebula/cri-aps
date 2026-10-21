#
# Copyright (C) 2025 by frePPLe bv
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

import ast
import os
import psutil
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile


from django.apps import apps
from django.conf import settings
from django.db import connections, DEFAULT_DB_ALIAS
from django.utils.encoding import force_str
from django.utils.text import capfirst

from freppledb.common.models import Scenario
from freppledb.common.utils import get_databases


# Apps holding planning data, and apps holding configuration.
BULK_DATA_APPS = ("input", "forecast")
BULK_ADMIN_APPS = ("common", "reportmanager")

# Infrastructure tables. Erasing the scenario registry breaks the installation,
# and the rest is per-user state rather than model data.
BULK_NEVER = (
    "common.scenario",
    "common.userpreference",
    "common.follower",
    "common.notification",
)


def getBulkModels(database=None):
    """
    The models that the erase and export screens can act on, as
    (label, "app.model", has_rows) triples, split into data and admin tables.

    This used to be derived from the menu, which silently produces an empty
    list on a deployment with a customized menu: custom menu entries carry no
    model. The app registry is the reliable source.
    """
    from freppledb.common.report import EXCLUDE_FROM_BULK_OPERATIONS

    # Which tables actually hold rows, so the screens can preselect them
    filled = set()
    if database:
        try:
            with connections[database].cursor() as cursor:
                cursor.execute(
                    """
                    select tablename from (
                      select c.relname as tablename,
                        query_to_xml(
                          format('select 1 as cnt from %I.%I limit 1', n.nspname, c.relname),
                          false, true, ''
                          ) as xml_count
                      from pg_class c
                      inner join pg_namespace n on n.oid = c.relnamespace
                      where n.nspname = 'public'
                        and c.relkind = 'r'
                      ) s
                    where xml_count is document
                    """
                )
                filled = set(i[0] for i in cursor)
        except Exception:
            # Preselection is a convenience, never a reason to fail the screen
            filled = set()

    result = {"data": [], "admin": []}
    for group, app_labels in (
        ("data", BULK_DATA_APPS),
        ("admin", BULK_ADMIN_APPS),
    ):
        for app_label in app_labels:
            try:
                config = apps.get_app_config(app_label)
            except LookupError:
                # App not installed in this deployment
                continue
            for model in config.get_models(include_auto_created=False):
                if model in EXCLUDE_FROM_BULK_OPERATIONS or not model._meta.managed:
                    continue
                name = "%s.%s" % (model._meta.app_label, model._meta.model_name)
                if name in BULK_NEVER:
                    continue
                result[group].append(
                    (
                        capfirst(force_str(model._meta.verbose_name)),
                        name,
                        model._meta.db_table in filled,
                    )
                )
        result[group].sort()
    return result


def writeFileAtomically(file_path, content, validate_python=False):
    """
    Replace the contents of a file in one atomic step.

    The configuration files touched here are read by every process frePPLe
    spawns. A half-written djangosettings.py takes the whole installation down,
    so we never write in place: the new content is built in memory, optionally
    parsed to prove it is valid python, written to a temporary file next to the
    original and only then moved over it. A failure at any point leaves the
    original file untouched.
    """
    if validate_python:
        # Raises SyntaxError, and the original file is still intact
        ast.parse(content)
    folder = os.path.dirname(file_path) or "."
    handle, tmp_path = tempfile.mkstemp(
        dir=folder, prefix=".%s-" % os.path.basename(file_path), suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        # Carry over the ownership and permissions of the file we replace
        original = os.stat(file_path)
        os.chmod(tmp_path, stat.S_IMODE(original.st_mode))
        try:
            os.chown(tmp_path, original.st_uid, original.st_gid)
        except (AttributeError, PermissionError):
            # Not root, or not a posix filesystem
            pass
        os.replace(tmp_path, file_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# This function will update the number of scenarios in the djangosettings.py file.
# The DATABASES variable must be using the "for i in range(x)" syntax
# Returns an error code
# 0: no error
# 1: not valid as scenario count drops below the minimum
# 2: not valid as scenario count exceeds the maximum
# 3: scenario to delete is not free
# 4: invalid djangosettings.py format
# 5: unknown error
# 6: the scenario table and djangosettings.py disagree on the scenario count
def updateScenarioCount(addition=True):
    using_apache = "runserver" not in sys.argv
    # The scenario table is filled in lazily from the settings file. A scenario
    # that was just added is therefore still missing here, which would make the
    # count below disagree with the settings file and reject a valid request.
    Scenario.syncWithSettings()
    scenario_count = Scenario.objects.using(DEFAULT_DB_ALIAS).count()
    min_scenarios = (
        settings.MIN_NUMBER_OF_SCENARIOS
        if hasattr(settings, "MIN_NUMBER_OF_SCENARIOS")
        else 2
    )
    max_scenarios = (
        settings.MAX_NUMBER_OF_SCENARIOS
        if hasattr(settings, "MAX_NUMBER_OF_SCENARIOS")
        else 30
    )
    if not addition and scenario_count - 1 < min_scenarios:
        return 1
    if addition and scenario_count + 1 > max_scenarios:
        return 2
    if not addition:
        last_scenario = (
            Scenario.objects.using(DEFAULT_DB_ALIAS)
            .filter(name=f"scenario{scenario_count-1}")
            .first()
        )
        if not last_scenario or last_scenario.status != "Free":
            return 3

    # Update djangosettings.py file
    file_path = os.path.join(settings.FREPPLE_CONFIGDIR, "djangosettings.py")

    inside_databases = False
    brace_depth = 0
    # Regex to match "for i in range(n)"
    range_pattern = re.compile(r"for\s+i\s+in\s+range\((\d+)\)")
    with open(file_path, "r") as file:
        lines = file.readlines()
    new_val = None
    found = False

    # The new content is assembled in memory. Nothing is written to disk until
    # we have a complete and valid result, so an early return or an unexpected
    # exception can never leave a truncated settings file behind.
    new_lines = []
    for line in lines:
        # Detect the start of DATABASES dict
        if "DATABASES" in line and "=" in line and "{" in line:
            inside_databases = True
            brace_depth = line.count("{") - line.count("}")

        elif inside_databases:
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                inside_databases = False

        # Only modify range inside DATABASES block
        if inside_databases and not found:
            match = range_pattern.search(line)
            if match:
                found = True
                current_val = int(match.group(1))
                if current_val != scenario_count:
                    return 6
                new_val = current_val + 1 if addition else current_val - 1
                line = range_pattern.sub(f"for i in range({new_val})", line)
        new_lines.append(line)

    if not found:
        return 4

    try:
        writeFileAtomically(file_path, "".join(new_lines), validate_python=True)
    except SyntaxError:
        # Our edit would have produced a broken settings file
        return 4
    except Exception:
        return 5

    # Update the apache configuration file
    file_path = "/etc/apache2/sites-enabled/z_frepple.conf"
    if using_apache and os.access(file_path, os.W_OK):
        # Regex to match 'Proxypass "/ws/' and 'Proxypass "/svc/'
        range_pattern = re.compile(r'Proxypass\s+"\/(svc|ws)\/')
        with open(file_path, "r") as file:
            lines = file.readlines()
        updated = False
        service_port = int(
            get_databases()[DEFAULT_DB_ALIAS]["FREPPLE_PORT"].split(":")[-1]
        )
        # Same approach as above: build the whole block first, write it in one go
        new_lines = []
        for line in lines:
            if range_pattern.search(line):
                if updated:
                    continue
                updated = True
                for i in range(new_val):
                    scenario = "default" if i == 0 else f"scenario{i}"
                    new_lines.append(
                        f'Proxypass "/ws/{scenario}/" "ws://localhost:{service_port+i}/ws/{scenario}/" retry=0\n'
                    )
                    new_lines.append(
                        f'Proxypass "/svc/{scenario}/" "http://localhost:{service_port+i}/" retry=0\n'
                    )
            else:
                new_lines.append(line)
        try:
            writeFileAtomically(file_path, "".join(new_lines))
        except Exception:
            # The settings file is already updated and valid. A stale apache
            # config only affects webservice proxying, so don't fail the whole
            # operation over it.
            pass

    if new_val:

        # We need to create/drop now the database
        # get the db name
        match = re.match(r"^(.*?)(\d+)$", get_databases()[DEFAULT_DB_ALIAS]["NAME"])
        if match:
            before_digits = match.group(1)
        else:
            return 4

        try:
            with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
                cursor.execute(
                    f"drop database if exists {before_digits}{new_val if not addition else new_val-1} WITH (FORCE)"
                )
                if addition:
                    cursor.execute(f"create database {before_digits}{new_val-1}")
                    # if settings.DATABASES[DEFAULT_DB_ALIAS]["SQL_ROLE"]:
                    #     cursor.execute(
                    #         f"grant connect on database {before_digits}{new_val-1} to {settings.DATABASES[DEFAULT_DB_ALIAS]["SQL_ROLE"]}"
                    #     )

            Scenario.syncWithSettings()
            if using_apache and shutil.which("apachectl"):
                # Development server automatically reloads the settings.
                # An apache server reload needs to be triggered manually.
                subprocess.run(["apachectl", "-k", "graceful"], check=True)
        except Exception:
            return 5

    return 0


def ReloadScheduler():
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info["name"]
            if name and "frepple-sched" in name:
                proc.send_signal(signal.SIGUSR1)
                return
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    print("Warning: No scheduler process found.")
