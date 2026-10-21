#
# Sieger navigation.
#
# Menu modules are loaded in REVERSED INSTALLED_APPS order, so this app -
# registered near the top of INSTALLED_APPS - runs LAST and gets the final
# word on a fully populated menu. That is what lets us remove core groups.
#
# Note: removing a group hides the link, it does not unregister the URL.
# Anyone typing the address still reaches the screen. Use Django
# permissions if a screen must genuinely be unreachable.
#

from django.utils.translation import gettext_lazy as _

from freppledb.menu import menu

# ---------------------------------------------------------------- remove
# frePPLe ships screens for distribution, retail, purchasing and
# forecasting. Sieger uses none of them - LN owns all of that.
for group in (
    "sales",
    "inventory",
    "capacity",
    "purchasing",
    "distribution",
    "manufacturing",
    "myreports",
):
    menu.removeGroup(group)

# ---------------------------------------------------------------- add
# One group, in THREE blocks separated by rules:
#
#   100-400  the four screens a planner opens every day
#   610-670  the data behind them - grids to search, filter and export,
#            plus the two frePPLe exception reports
#   810-850  the periodic reports management asks for
#
# Nothing is hidden: every screen Sieger asked for is still one click
# away. The blocks exist so the daily path is four items, not eighteen.
menu.addGroup("planning", label=_("planning"), index=50)

# ------------------------------------------------------------ daily
menu.addItem(
    "planning",
    "sieger_home",
    url="/",
    label=_("home"),
    index=100,
    prefix=True,
)
# Our own plan board. frePPLe's Gantt lane-packs client-side on data
# sorted by database id, which is only correct if it arrives sorted by
# start date - so rows grew ~23 lanes deep against a true concurrency of
# 1, and it drew the whole two-month horizon at once, making four-hour
# jobs three pixels wide. views.schedule has the full account.
menu.addItem(
    "planning",
    "sieger_planboard",
    url="/machine-schedule/",
    label=_("plan board"),
    index=200,
    prefix=True,
)
# The project timeline - one order at a time, reached from the build
# graph's "timeline" button and the release window. The old overview
# ("every order as one bar", /projects/) left the menu on 12 Oct 2026:
# at 600-10,000 orders it was an endless scroll nobody could use, and
# the home shipment summary now answers the same question at the
# altitude planners actually think in. The URL still works.
# The pre-release gate: late orders, overloads and hot machines must be
# clear or consciously accepted, then the window is pushed. release.py.
menu.addItem(
    "planning",
    "sieger_release",
    url="/release/",
    label=_("release window"),
    index=400,
    prefix=True,
)

# --------------------------------------------------- data and detail
# Grids and drill-downs. Not screens a planner reads front to back -
# where they go to search one order, export a list, or ask "why".
menu.addItem("planning", "sieger_sep", separator=True, index=500)
menu.addItem(
    "planning",
    "sieger_orders",
    url="/production-orders/",
    label=_("production orders"),
    index=610,
    prefix=True,
)
menu.addItem(
    "planning",
    "sieger_schedule",
    url="/data/input/manufacturingorder/",
    label=_("schedule"),
    index=620,
    prefix=True,
)
# "machine load" (frePPLe's /resource/ report) was removed from this menu
# on 30 Sep 2026: utilisation already appears on the Bottlenecks screen and
# as the ranked candidate table on Constraints, and three screens answering
# "how busy are my machines" is how the menu came to feel redundant. The
# URL still works - Bottlenecks, the cockpit and the release panel deep-link
# to /resource/<machine>/ for the per-machine drill-down.
# The build graph answers the other half of the plan board's question:
# the board says what each MACHINE is doing, this says what has to
# happen for one ORDER, and when. See buildgraph.py.
menu.addItem(
    "planning",
    "sieger_buildgraph",
    url="/build-graph/",
    label=_("build graph"),
    index=640,
    prefix=True,
)
# frePPLe's own Gantt (/data/input/operationplanresource/ with showGantt) was
# removed from this menu on 30 Sep 2026: it duplicated the Plan Board - the
# menu hint itself said "kept for cross-checking" - and the custom board is
# now the trusted screen. The URL still works if a cross-check is ever
# needed, and sieger-gantt.js still patches it when opened.
menu.addItem(
    "planning",
    "sieger_exceptions",
    url="/problem/",
    label=_("exceptions"),
    index=660,
    prefix=True,
)
menu.addItem(
    "planning",
    "sieger_why",
    url="/constraint/",
    label=_("why late"),
    index=670,
    prefix=True,
)

# --------------------------------------------------------- reports
# Phase 1/2 reports - the questions management asks, one screen each,
# in the order they escalate: what completes when, where time is lost,
# which group chokes which week, is the plan holding, what would change.
menu.addItem("planning", "sieger_sep2", separator=True, index=700)
menu.addItem(
    "planning",
    "sieger_constraints",
    url="/constraints/",
    label=_("constraints"),
    index=805,
    prefix=True,
)
menu.addItem(
    "planning",
    "sieger_improve",
    url="/improve/",
    label=_("what to do next"),
    index=806,
    prefix=True,
)
# "delivery sequences" was replaced by "deliver on time" on 7 Oct 2026,
# and "deliver on time" itself was absorbed into the build graph the same
# day: the graph's shipment landing page carries the same cards and its
# blocker rail carries the same fixes, so a separate menu entry would
# answer a question the graph already answers. Both URLs (/sequences/,
# /delivery/) stay reachable, per this menu's hide-don't-unregister
# practice.
# The latest each shipment can start and still meet its date - arithmetic
# over its own routing and BOM, with a Measure button into improve.prove.
menu.addItem(
    "planning",
    "sieger_startdates",
    url="/start-dates/",
    label=_("start dates"),
    index=815,
    prefix=True,
)
# Flow time, bottlenecks, adherence and scenario comparison left the
# menu on 13 Oct 2026 to keep the planner's daily view lean. Their URLs
# still work (hide, don't unregister). Re-enable bottlenecks and
# scenario comparison first when the capacity what-if work resumes -
# bottlenecks is the utilization evidence table, compare is the
# before/after readout every what-if needs.

# "Falcon scenarios" was taken off this menu on 15 Oct 2026. It compared
# three ways of reading the workbook's BOM quantities (S1 raw, S2
# normalised, S3 work-order-led) - a question about how to LOAD the data,
# answered during the build, not something a planner acts on day to day.
# /falcon-scenarios/ still answers, per this menu's hide-don't-unregister
# practice, and scenarios 8-10 still hold the three plans.

# Master data stays reachable - when a planner asks "why did this job go to
# that machine?", they need the routing. One group, four screens.
menu.addGroup("reference", label=_("reference"), index=60)
menu.addItem(
    "reference",
    "sieger_items",
    url="/data/input/item/",
    label=_("parts"),
    index=100,
    prefix=True,
)
menu.addItem(
    "reference",
    "sieger_routings",
    url="/data/input/operation/",
    label=_("routings"),
    index=200,
    prefix=True,
)
# "why did this job go to that machine?" - this screen is the answer.
menu.addItem(
    "reference",
    "sieger_opres",
    url="/data/input/operationresource/",
    label=_("operation to machine"),
    index=250,
    prefix=True,
)
menu.addItem(
    "reference",
    "sieger_machines",
    url="/data/input/resource/",
    label=_("machines"),
    index=300,
    prefix=True,
)
menu.addItem(
    "reference",
    "sieger_calendars",
    url="/data/input/calendar/",
    label=_("shift calendars"),
    index=400,
    prefix=True,
)
# The calendar list is only names. The actual start/end times live here.
menu.addItem(
    "reference",
    "sieger_shifttimes",
    url="/data/input/calendarbucket/",
    label=_("shift times"),
    index=450,
    prefix=True,
)
# Empty until LN supplies a bill of materials, but the screen must exist
# for the day it does.
menu.addItem(
    "reference",
    "sieger_opmat",
    url="/data/input/operationmaterial/",
    label=_("materials per step"),
    index=500,
    prefix=True,
)

# The 'admin' and 'help' groups are deliberately left untouched - someone has
# to manage users, scenarios and scheduled tasks.
