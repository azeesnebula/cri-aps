"""Create/export the isolated C.R.I planning scenario without replacing data."""
from django.core.management.base import BaseCommand, CommandError

from cri_ui.planning_scenario import check_database, export_inputs, load


class Command(BaseCommand):
    help = "Load an empty C.R.I cri0 database with the reproducible 19 October 2026 pump-factory planning scenario."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")
        parser.add_argument("--load", action="store_true", help="Create inputs; refuses non-empty planning data")
        parser.add_argument("--export", action="store_true", help="Export the input snapshot and saved plan")
        parser.add_argument("--output", default="/var/log/frepple/cri-planning-20261019")

    def handle(self, **options):
        db = options["database"]
        check_database(db)
        if not options["load"] and not options["export"]:
            raise CommandError("Specify --load for first-time input creation or --export for the saved plan.")
        if options["load"]:
            result = load(db)
            self.stdout.write(str({k: v for k, v in result.items() if k != "gross_material_requirements"}))
        export_inputs(options["output"], db)
        if options["export"]:
            from cri_ui.planning_report import export_plan
            result = export_plan(options["output"], db)
            self.stdout.write(str(result))
            if not result["passed"]:
                raise CommandError("Saved plan failed validation. See validation.json and schedule workbook.")
        self.stdout.write(self.style.SUCCESS("Planning scenario files: " + options["output"]))
