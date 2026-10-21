#
# Claims "/" for the guided cockpit.
#
# This app sits directly after freppledb.boot in INSTALLED_APPS, and the
# root urlconf appends each app's patterns in INSTALLED_APPS order. Ours is
# therefore registered before freppledb.common's path("") and wins the
# match. frePPLe's own cockpit stays reachable at /cockpit/ .
#

from django.urls import path

from . import (
    buildgraph,
    constraints,
    delivery,
    improve,
    moves,
    release,
    reports,
    shipmap,
    startdates,
    timeline,
    views,
    workbookimport,
)

autodiscover = True

urlpatterns = [
    path("", views.cockpit, name="sieger_cockpit"),
    path("production-orders/", views.production_orders, name="sieger_production_orders"),
    # Purpose-built replacement for frePPLe's Gantt. Named distinctly so
    # it cannot collide with a core route on a future upgrade.
    path("machine-schedule/", views.schedule, name="sieger_schedule"),
    # Detail behind a bar click. Kept under the board's own prefix so the
    # two always ship together.
    path(
        "machine-schedule/op/<path:ref>/",
        views.operation_detail,
        name="sieger_op_detail",
    ),
    # ------------------------------------------------------------------
    # Drag-and-drop rescheduling. validate judges a drop, apply pins the
    # job and replans, undo restores the snapshot. See moves.py for why
    # these are ours and not frePPLe's endpoints.
    # ------------------------------------------------------------------
    path(
        "machine-schedule/move/validate/",
        moves.move_validate,
        name="sieger_move_validate",
    ),
    path("machine-schedule/move/apply/", moves.move_apply, name="sieger_move_apply"),
    path("machine-schedule/move/undo/", moves.move_undo, name="sieger_move_undo"),
    path(
        "machine-schedule/move/dismiss/",
        moves.move_dismiss,
        name="sieger_move_dismiss",
    ),
    path(
        "machine-schedule/replan/status/",
        moves.replan_status,
        name="sieger_replan_status",
    ),
    # ------------------------------------------------------------------
    # Project timeline. One demand IS one project at Sieger; the overview
    # is every order as one bar against its due date, the detail is one
    # row per operation. The literal route must precede the converter.
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Release window. The panel is read-only; ack/ and action/ are the
    # app's only state-changing POSTs and carry their own permission
    # checks (input.change_operationplan).
    # ------------------------------------------------------------------
    path("constraints/", constraints.constraints, name="sieger_constraints"),
    path("constraints/save/", constraints.save, name="sieger_constraints_save"),
    path("constraints/run/", constraints.run, name="sieger_constraints_run"),
    path("constraints/status/", constraints.status, name="sieger_constraints_status"),
    # What to do next - reads the plan and suggests what would help.
    path("improve/", improve.improve, name="sieger_improve"),
    path("improve/prove/", improve.prove, name="sieger_improve_prove"),
    path("improve/status/", improve.status, name="sieger_improve_status"),
    path("improve/dismiss/", improve.dismiss, name="sieger_improve_dismiss"),
    path("release/", release.panel, name="sieger_release"),
    path("release/ack/", release.ack, name="sieger_release_ack"),
    path("release/action/", release.release_action, name="sieger_release_action"),
    path("projects/", timeline.projects, name="sieger_projects"),
    path(
        "projects/<path:demand>/",
        timeline.project_timeline,
        name="sieger_project_timeline",
    ),
    # ------------------------------------------------------------------
    # Build graph. A separate sub-application: pick a machine, then see
    # every operation that produces it laid out on a real date axis.
    # Nothing here touches the routes above.
    # ------------------------------------------------------------------
    # The map IS the build-graph screen now; the per-order picker
    # moved to build-graph/orders/.
    path("build-graph/", shipmap.shipmap, name="sieger_build_select"),
    path("build-graph/orders/", buildgraph.product_list,
         name="sieger_build_orders"),
    # Production-run detail is registered BEFORE the <path:demand> route -
    # 'run' is a legal demand name as far as the converter is concerned, so
    # the more specific pattern has to win the match.
    path(
        "build-graph/run/<path:ref>/",
        buildgraph.run_detail,
        name="sieger_build_run",
    ),
    # The shipment graph - the heart screen. Registered before the
    # <path:demand> catch-all for the same reason run/ is.
    # The shipment map - the heart screen. Everything loads on demand,
    # so these are small JSON calls rather than one huge payload.
    path("build-graph/map/ships/", shipmap.ships_data, name="sieger_map_ships"),
    path("build-graph/map/search/", shipmap.search_data,
         name="sieger_map_search"),
    path("build-graph/map/<str:tag>/allruns/", shipmap.all_runs_data,
         name="sieger_map_allruns"),
    path(
        "build-graph/map/<str:tag>/sections/",
        shipmap.sections_data,
        name="sieger_map_sections",
    ),
    path(
        "build-graph/map/<str:tag>/<str:section>/runs/",
        shipmap.runs_data,
        name="sieger_map_runs",
    ),
    path(
        "build-graph/<path:demand>/data/",
        buildgraph.build_graph_data,
        name="sieger_build_data",
    ),
    path(
        "build-graph/<path:demand>/",
        buildgraph.build_graph,
        name="sieger_build_graph",
    ),
    # ------------------------------------------------------------------
    # Phase 1/2 reports. Server-rendered, one SQL query each - see
    # reports.py for what each screen answers.
    # ------------------------------------------------------------------
    # Shipment-first: the promise, the blocking machines, the causes.
    # Supersedes /sequences/ in the menu; the old URL stays alive below.
    path("delivery/", delivery.delivery, name="sieger_delivery"),
    path("sequences/", reports.sequences, name="sieger_sequences"),
    # The latest each shipment can start, and why. Arithmetic floor per
    # shipment; measuring runs through improve.prove on a scenario copy.
    path("start-dates/", startdates.start_dates, name="sieger_startdates"),
    path("shipment/", views.shipment_detail, name="sieger_shipment_select"),
    path(
        "shipment/<path:tag>/",
        views.shipment_detail,
        name="sieger_shipment",
    ),
    path("flow/", reports.flow, name="sieger_flow"),
    path("bottlenecks/", reports.bottlenecks, name="sieger_bottlenecks"),
    path("adherence/", reports.adherence, name="sieger_adherence"),
    path("compare/", reports.compare, name="sieger_compare"),
    # Dry-run inspection of a planner-uploaded Infor-LN workbook. Driven
    # by the "Import planning workbook" panel on the Execute screen.
    path(
        "planning-workbook/check/",
        workbookimport.check,
        name="sieger_workbook_check",
    ),
]
