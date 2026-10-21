"""
Puts "Import planning workbook" on the Execute screen.

WHY THIS EXISTS
---------------
The Infor-LN extract the planners work from is a human-readable workbook:
numbered tabs, a prose title row, a legend block, two header rows stacked on
each other. frePPLe's own "Import a spreadsheet" cannot read it - it wants one
tab per model, named after the model, with field names in row 1. Loading the
LN workbook therefore meant running loader/load_sieger.py from a developer's
laptop, which is not a workflow a planner can own.

This panel moves that job into the application: the planner uploads the
workbook they already have and the APS tells them what it found.

WHAT IT DOES TODAY
------------------
Dry run only. It reports which sheets and columns were recognised, how many
rows each holds, and what would have to be invented - and it writes nothing.
That is deliberate: the mapping has to earn trust against real files before it
is allowed near a database, and "will my sheet load?" is independently useful.

The frePPLe Execute screen builds its panel list from get_commands(), so any
installed app that ships a management command with an `index` and a `getHTML`
appears there. No core file is touched.
"""

from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from cri_ui.workbook import SiegerWorkbook, WorkbookProblem


class Command(BaseCommand):
    help = """
    Inspect a compatible Infor-LN planning workbook and report what frePPLe would
    make of it. Reads the file only; nothing is written to the database.
    """

    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("file", help="path to the .xlsx workbook")

    def handle(self, **options):
        workbook = SiegerWorkbook(options["file"])
        try:
            workbook.read()
        except WorkbookProblem as e:
            self.stderr.write(str(e))
            return
        report = workbook.report()
        for sheet in report["sheets"]:
            self.stdout.write(
                "%-11s %-30s rows=%-6d data starts at row %s"
                % (
                    sheet["key"],
                    sheet["sheet"] or "NOT FOUND",
                    sheet["rows"],
                    sheet["header_row"],
                )
            )
            if sheet["missing"]:
                self.stdout.write("            missing: %s" % ", ".join(sheet["missing"]))
        for note in report["notes"]:
            self.stdout.write("note: %s" % note)
        for problem in report["blocking"]:
            self.stderr.write("BLOCKING: %s" % problem)
        if not report["blocking"]:
            self.stdout.write("The workbook can be read.")

    # -- Execute screen panel --------------------------------------------

    title = _("Import planning workbook")
    index = 2050  # directly after frePPLe's own "Import a spreadsheet" (2000)

    @staticmethod
    def getHTML(request):
        if not request.user.has_perm("auth.run_db"):
            return None
        return render_to_string(
            "sieger_ui/importworkbook_panel.html", request=request
        )
