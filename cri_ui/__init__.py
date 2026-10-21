#
# Sieger APS - user interface app
#
# Simplifies the frePPLe navigation down to the screens a Sieger planner
# actually uses, and replaces the home page with a guided daily workflow.
#
# Nothing in freppledb/ is modified. This app works purely through the
# extension points frePPLe autodiscovers: menu.py, urls.py, templates and
# static files.
#

frepple_app = {
    "summary": "C.R.I Pumps production planning",
    "description": """
        <p>A dedicated C.R.I Pumps production planning workspace, with a
        guided home screen that follows the planner's daily sequence:
        check the order book, run the plan, read the schedule, check the
        load, review exceptions.</p>
        """,
}
