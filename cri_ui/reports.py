#
# Phase 1 & 2 reporting screens.
#
# Five server-rendered reports, in the order a planner meets them:
#
#   /sequences/    when does each delivery sequence complete?      (phase 1)
#   /flow/         where does time go - working vs waiting?        (phase 2)
#   /bottlenecks/  which machine group chokes which week?          (phase 2)
#   /adherence/    is the plan on time, and is it drifting?        (phase 2)
#   /compare/      what changed between live and a what-if?        (phase 2)
#
# Everything is computed from the same tables the planning engine writes -
# nothing here keeps state of its own. Heavy aggregation is one SQL query
# per screen; Python only shapes rows for the template.
#

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import DEFAULT_DB_ALIAS, connections
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from freppledb.common.models import Scenario
from freppledb.common.utils import get_databases
from freppledb.input.models import Demand

from .views import _plan_now


def _db(request):
    return getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS


def _rows(db, sql, params=None):
    # Decimals become floats HERE, once - every screen mixes these values
    # with float literals, and Decimal * float raises.
    with connections[db].cursor() as c:
        c.execute(sql, params or [])
        cols = [d[0] for d in c.description]
        return [
            {
                k: (float(v) if isinstance(v, Decimal) else v)
                for k, v in zip(cols, r)
            }
            for r in c.fetchall()
        ]


def _hours(seconds):
    return round((seconds or 0) / 3600.0, 1)


def _days(seconds):
    return round((seconds or 0) / 86400.0, 1)


# ======================================================================
# Delivery sequences - when does each one complete?
# ======================================================================
@login_required(login_url="/data/login/")
def sequences(request):
    db = _db(request)
    now = _plan_now(db)

    # Demand.category carries the delivery-sequence tag the moment the LN
    # extract supplies it; until then everything lands in the untagged row.
    rows = _rows(
        db,
        """
        select coalesce(nullif(category,''),'(untagged)') as tag,
               count(*) as orders,
               sum(quantity) as qty,
               min(due) as first_due,
               max(due) as last_due,
               max(deliverydate) as completes,
               count(*) filter (where delay > interval '0') as late,
               max(extract(epoch from delay)) as worst_delay,
               count(*) filter (where deliverydate is null) as unplanned
        from demand
        group by 1
        order by min(due)
        """,
    )
    seqs = []
    for r in rows:
        seqs.append(
            {
                "tag": r["tag"],
                "orders": r["orders"],
                "qty": int(r["qty"] or 0),
                "first_due": r["first_due"],
                "last_due": r["last_due"],
                "completes": r["completes"],
                "late": r["late"],
                "unplanned": r["unplanned"],
                "worst_delay_days": _days(r["worst_delay"]),
                "ok": r["late"] == 0 and r["unplanned"] == 0,
            }
        )
    tagged = [s for s in seqs if s["tag"] != "(untagged)"]

    return render(
        request,
        "sieger_ui/sequences.html",
        {
            "title": _("delivery sequences"),
            "now": now,
            "seqs": seqs,
            "tagged_count": len(tagged),
            "order_total": sum(s["orders"] for s in seqs),
        },
    )


# ======================================================================
# Flow time - working vs waiting, per production run.
# ======================================================================
@login_required(login_url="/data/login/")
def flow(request):
    db = _db(request)
    now = _plan_now(db)

    jobs = _rows(
        db,
        """
        with job as (
          select p.reference, p.item_id, p.operation_id,
                 p.startdate, p.enddate,
                 extract(epoch from (p.enddate - p.startdate)) as espan,
                 coalesce(sum(extract(epoch from (c.enddate - c.startdate))), 0)
                   as work,
                 count(c.reference) as steps
          from operationplan p
          left join operationplan c on c.owner_id = p.reference
          where p.type = 'MO' and p.enddate > p.startdate
          group by p.reference, p.item_id, p.operation_id,
                   p.startdate, p.enddate)
        select * from job
        """,
    )
    n = len(jobs)
    if n:
        tot_span = sum(j["espan"] for j in jobs)
        tot_work = sum(min(j["work"], j["espan"]) for j in jobs)
        avg_span_d = _days(tot_span / n)
        avg_work_d = _days(tot_work / n)
        pct_working = round(100.0 * tot_work / tot_span, 1) if tot_span else 0.0
    else:
        avg_span_d = avg_work_d = pct_working = 0.0

    # Elapsed-days histogram - the shape management asked about (30-60d).
    buckets = [(0, 3), (3, 7), (7, 14), (14, 30), (30, 60), (60, 9999)]
    hist = []
    for lo, hi in buckets:
        c = sum(1 for j in jobs if lo <= j["espan"] / 86400.0 < hi)
        hist.append(
            {
                "label": f"{lo}-{hi}d" if hi < 9999 else f"{lo}d+",
                "count": c,
                "pct": round(100.0 * c / n, 1) if n else 0,
            }
        )
    maxpct = max((h["pct"] for h in hist), default=0) or 1

    # The jobs that wait the longest - each one is parked WIP.
    waiters = sorted(jobs, key=lambda j: j["espan"] - j["work"], reverse=True)[:20]
    top = [
        {
            "ref": j["reference"],
            "item": j["item_id"] or "",
            "operation": j["operation_id"] or "",
            "start": j["startdate"],
            "end": j["enddate"],
            "elapsed_d": _days(j["espan"]),
            "working_h": _hours(j["work"]),
            "waiting_d": _days(j["espan"] - j["work"]),
            "steps": j["steps"],
        }
        for j in waiters
    ]

    return render(
        request,
        "sieger_ui/flow.html",
        {
            "title": _("flow time"),
            "now": now,
            "jobs": n,
            "avg_span_d": avg_span_d,
            "avg_work_d": avg_work_d,
            "avg_wait_d": round(avg_span_d - avg_work_d, 1),
            "pct_working": pct_working,
            "hist": hist,
            "maxpct": maxpct,
            "top": top,
        },
    )


# ======================================================================
# Bottlenecks - machine-group utilisation per week.
# ======================================================================
WEEKS_SHOWN = 10


@login_required(login_url="/data/login/")
def bottlenecks(request):
    db = _db(request)
    now = _plan_now(db)
    try:
        threshold = max(10, min(int(request.GET.get("pct", 85)), 200))
    except (TypeError, ValueError):
        threshold = 85

    # Capacity comes from out_resourceplan, NOT from a hardcoded plant
    # week. That table is the engine's own per-resource, per-day account
    # in seconds: 'available' is derived from the machine's calendar, so
    # it already honours SIEGER-2SHIFT, non-working days and the
    # resource maximum - and it keeps honouring them when a holiday or a
    # real per-machine calendar arrives (requirement A6). This screen
    # used to divide by a flat 96 h x machine count, which agreed with
    # the plan board and the release panel only by luck and would have
    # drifted silently the moment the calendar changed.
    #
    # The same arithmetic as release.py's hot-machine checkpoint:
    #   utilisation = (load + setup) / available
    # Setup is included here for the first time; the old query ignored
    # it entirely.
    #
    # UNITS: out_resourceplan stores HOURS, not seconds. frePPLe's own
    # resource report proves it - getUnits() divides by 1.0 for "hours",
    # 24 for days, 168 for weeks (output/views/resource.py:265-278) - and
    # the raw rows agree: a two-shift day reads available=16, unavailable=8.
    # Do NOT divide by 3600 here; the ratio would survive it but every
    # displayed hour figure would be 3600x too small.
    #
    # A machine's group: its owner when the owner itself hangs under a
    # section, else the machine IS the group (the self-named ones).
    # Only type='default' rows are machines - sections and groups are
    # loaded as 'infinite', as are the vendor pools, so this filter is
    # what keeps them out of both numerator and denominator.
    load = _rows(
        db,
        """
        select case when gp.owner_id is not null then r.owner_id
                    else r.name end                             as grp,
               date_trunc('week', rp.startdate)                  as wk,
               sum(coalesce(rp.load, 0) + coalesce(rp.setup, 0)) as busy_h,
               sum(coalesce(rp.available, 0))                    as avail_h,
               count(distinct rp.resource)                       as machines
        from out_resourceplan rp
        join resource r on r.name = rp.resource
        left join resource gp on gp.name = r.owner_id
        where r.type = 'default'
          and rp.startdate >= %s and rp.startdate < %s
        group by 1, 2
        order by 1, 2
        """,
        [now - timedelta(days=7), now + timedelta(weeks=WEEKS_SHOWN)],
    )

    week_starts = sorted({r["wk"] for r in load})[:WEEKS_SHOWN]
    by_grp = {}
    for r in load:
        # A group with no available hours in a week - every machine in it
        # is off shift - has no meaningful percentage; leave the cell empty
        # rather than printing a divide-by-zero zero.
        if r["wk"] not in week_starts or not r["avail_h"]:
            continue
        g = by_grp.setdefault(
            r["grp"], {"grp": r["grp"], "machines": r["machines"], "cells": {}}
        )
        g["machines"] = max(g["machines"], r["machines"])
        util = round(100.0 * r["busy_h"] / r["avail_h"], 0)
        g["cells"][r["wk"]] = {
            "util": int(util),
            "hours": round(r["busy_h"]),
            "avail": round(r["avail_h"]),
            "hot": util >= threshold,
            "over": util >= 100,
        }

    groups = []
    for g in by_grp.values():
        g["row"] = [g["cells"].get(w) for w in week_starts]
        g["peak"] = max((c["util"] for c in g["cells"].values()), default=0)
        g["hot_weeks"] = sum(1 for c in g["cells"].values() if c["hot"])
        groups.append(g)
    groups.sort(key=lambda g: -g["peak"])

    return render(
        request,
        "sieger_ui/bottlenecks.html",
        {
            "title": _("bottlenecks"),
            "now": now,
            "weeks": week_starts,
            "groups": groups,
            "threshold": threshold,
            "hot_total": sum(g["hot_weeks"] for g in groups),
        },
    )


# ======================================================================
# Adherence - on-time by week, and drift against the archived plan.
# ======================================================================
@login_required(login_url="/data/login/")
def adherence(request):
    db = _db(request)
    now = _plan_now(db)

    weeks = _rows(
        db,
        """
        select date_trunc('week', due) as wk,
               count(*) as orders,
               count(*) filter (where delay <= interval '0') as ontime,
               count(*) filter (where delay > interval '0') as late
        from demand
        where due >= %s and due < %s
        group by 1 order by 1
        """,
        [now - timedelta(days=7), now + timedelta(weeks=12)],
    )
    for w in weeks:
        w["pct"] = round(100.0 * w["ontime"] / w["orders"], 0) if w["orders"] else 0

    total = sum(w["orders"] for w in weeks)
    ontime = sum(w["ontime"] for w in weeks)
    overall = round(100.0 * ontime / total, 1) if total else 0.0

    # Drift against the newest archived snapshot. Confirming actuals
    # overwrites planned dates, so the snapshot is the only baseline -
    # that is why the pipeline archives right after every plan run.
    snapshot = None
    drift = None
    movers = []
    try:
        from freppledb.archive.models import ArchiveManager, ArchivedDemand

        mgr = (
            ArchiveManager.objects.using(db).order_by("-snapshot_date").first()
        )
        if mgr:
            snapshot = mgr.snapshot_date
            old = {
                d["name"]: d["deliverydate"]
                for d in ArchivedDemand.objects.using(db)
                .filter(snapshot_date=mgr)
                .values("name", "deliverydate")
            }
            later = earlier = same = 0
            for d in (
                Demand.objects.using(db)
                .filter(name__in=list(old))
                .values("name", "item_id", "due", "deliverydate")
            ):
                a, b = old.get(d["name"]), d["deliverydate"]
                if not a or not b:
                    continue
                delta = (b - a).total_seconds()
                if abs(delta) < 3600:
                    same += 1
                else:
                    if delta > 0:
                        later += 1
                    else:
                        earlier += 1
                    movers.append(
                        {
                            "name": d["name"],
                            "item": d["item_id"],
                            "due": d["due"],
                            "was": a,
                            "is_now": b,
                            "shift_d": round(delta / 86400.0, 1),
                        }
                    )
            movers.sort(key=lambda m: -abs(m["shift_d"]))
            movers = movers[:20]
            drift = {"later": later, "earlier": earlier, "same": same}
    except Exception:
        snapshot = None

    return render(
        request,
        "sieger_ui/adherence.html",
        {
            "title": _("adherence"),
            "now": now,
            "weeks": weeks,
            "overall": overall,
            "total": total,
            "ontime": ontime,
            "late": total - ontime,
            "snapshot": snapshot,
            "drift": drift,
            "movers": movers,
        },
    )


# ======================================================================
# Scenario comparison - live plan vs a what-if sandbox.
# ======================================================================
@login_required(login_url="/data/login/")
def compare(request):
    db = _db(request)
    now = _plan_now(db)

    scenarios = [d for d in get_databases() if d != db]
    # Only scenarios the planner can actually open - the selector hides
    # anything marked Free, so the target list must agree with it. The
    # canonical status lives in the default database, not in the copies.
    in_use = set(
        Scenario.objects.using(DEFAULT_DB_ALIAS)
        .filter(status="In use")
        .values_list("name", flat=True)
    )
    scenarios = [d for d in scenarios if d in in_use]
    target = request.GET.get("scenario") or (scenarios[0] if scenarios else None)

    error = None
    summary = None
    movers = []
    if target not in scenarios:
        error = "No what-if scenario databases are configured."
        target = None
    else:
        try:
            base = {
                d["name"]: d
                for d in Demand.objects.using(db).values(
                    "name", "item_id", "due", "deliverydate", "delay"
                )
            }
            other = {
                d["name"]: d
                for d in Demand.objects.using(target).values(
                    "name", "deliverydate", "delay"
                )
            }
            if not other:
                error = (
                    f"Scenario '{target}' holds no orders - copy the live "
                    "database into it from Admin - Execute, change what you "
                    "want to test, and replan there."
                )
            else:
                zero = timedelta(0)
                later = earlier = same = 0
                base_late = sum(
                    1 for d in base.values() if (d["delay"] or zero) > zero
                )
                other_late = sum(
                    1 for d in other.values() if (d["delay"] or zero) > zero
                )
                for name, d in base.items():
                    o = other.get(name)
                    if not o or not d["deliverydate"] or not o["deliverydate"]:
                        continue
                    delta = (
                        o["deliverydate"] - d["deliverydate"]
                    ).total_seconds()
                    if abs(delta) < 3600:
                        same += 1
                        continue
                    if delta > 0:
                        later += 1
                    else:
                        earlier += 1
                    movers.append(
                        {
                            "name": name,
                            "item": d["item_id"],
                            "due": d["due"],
                            "live": d["deliverydate"],
                            "whatif": o["deliverydate"],
                            "shift_d": round(delta / 86400.0, 1),
                        }
                    )
                movers.sort(key=lambda m: -abs(m["shift_d"]))
                movers = movers[:25]
                summary = {
                    "orders": len(base),
                    "compared": later + earlier + same,
                    "later": later,
                    "earlier": earlier,
                    "same": same,
                    "base_late": base_late,
                    "other_late": other_late,
                    "late_delta": other_late - base_late,
                }
        except Exception as e:  # scenario DB missing / not migrated
            error = f"Scenario '{target}' is not usable: {e}"

    return render(
        request,
        "sieger_ui/compare.html",
        {
            "title": _("scenario comparison"),
            "now": now,
            "scenarios": scenarios,
            "target": target,
            "summary": summary,
            "movers": movers,
            "error": error,
        },
    )
