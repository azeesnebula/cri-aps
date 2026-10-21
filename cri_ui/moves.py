#
# Drag-and-drop rescheduling for the machine schedule board.
#
# The planner drags a bar to a new time and/or machine. The flow is:
#
#   POST move/validate/  - every concern with the drop, judged server-side
#                          (overlaps, shift calendar, capability, due date).
#                          Nothing is written.
#   POST move/apply/     - pin the job THERE (status 'confirmed', which the
#                          engine treats as frozen dates), remember how to
#                          undo it, and launch a constrained replan so the
#                          rest of the plan rearranges around the pin.
#   GET  replan/status/  - poll the runplan Task.
#   POST move/undo/      - put the old dates/machine/status back, replan.
#   POST move/dismiss/   - clear the before/after banner.
#
# WHY OUR OWN ENDPOINTS AND NOT frePPLe'S
# ---------------------------------------
# frePPLe's own writer (OperationPlanDetail.post, input/views/utils.py)
# saves with update_fields that omit "status" - a status change is
# silently dropped, so it cannot pin. It also swallows every error and
# answers "OK" unconditionally. And the before-metrics snapshot has to be
# taken in the same breath as the write, before the replan truncates
# out_problem. So the orchestration lives here, in one place.
#
# THE PIN
# -------
# 'confirmed' is the frozen status: the engine hard-sets those dates
# (freezeStatus) and a full replan deletes only 'proposed' operationplans.
# The parent routing MO is confirmed too - runplan NULLs the owner of any
# child whose parent is proposed before deleting (output/commands.py), so
# a confirmed step under a proposed parent would be orphaned and its
# routing regenerated beside it. Both writes use queryset.update() ON
# PURPOSE: model save() would cascade the status to every sibling step
# via propagateStatus, and the siblings must stay proposed so the engine
# is free to re-place them around the pin.
#
# THE UNDO SNAPSHOT lives in the session - deliberately not a table, so
# the app keeps its no-migrations deploy. Single-planner deployment makes
# per-browser undo acceptable; the move itself is durable data either way.
#

import json

from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db import DEFAULT_DB_ALIAS, transaction
from django.db.models import Count
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from freppledb.execute.management.commands.runworker import launchWorker
from freppledb.execute.models import Task
from freppledb.input.models import (
    Calendar,
    CalendarBucket,
    Demand,
    OperationPlan,
    OperationPlanResource,
    OperationResource,
    Resource,
)
from freppledb.output.models import Problem

from .buildgraph import _pegging
from .views import _plan_now

SESSION_KEY = "sieger_move"
MOVABLE_STATUS = ("proposed", "approved")
MOVABLE_TYPE = ("MO", "WO")
SNAP_SECONDS = 900  # a bar lands on 15-minute marks

RUNPLAN_ARGS = "--constraint=capa,mfg_lt,po_lt --plantype=1 --env=supply"


# ----------------------------------------------------------------- shared


def _db(request):
    return getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS


def _err(code, status, **extra):
    extra["error"] = code
    return JsonResponse(extra, status=status)


def _payload(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _parse_dt(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", ""))
    except ValueError:
        return None


def _iso(dt):
    return dt.isoformat() if dt else None


def _fmt(dt):
    return dt.strftime("%a %d %b, %H:%M") if dt else ""


def _running_replan(db):
    """The unfinished runplan Task, if one exists."""
    return (
        Task.objects.using(db)
        .filter(name="runplan", finished__isnull=True)
        .exclude(status__in=("Done", "Failed", "Canceled"))
        .order_by("-id")
        .first()
    )


def _launch_replan(db, user):
    """Queue a constrained replan exactly the way the execute screen does."""
    task = Task(
        name="runplan",
        submitted=timezone.now(),
        status="Waiting",
        user=user,
        arguments=RUNPLAN_ARGS,
    )
    task.save(using=db)
    launchWorker(database=db)
    return task.id


def _snapshot_metrics(db):
    """
    Problem counts by name plus the late-order count - taken BEFORE the
    replan wipes out_problem, and again after it finishes, so the banner
    can say what the move actually did to the plan.
    """
    problems = {
        r["name"]: r["n"]
        for r in Problem.objects.using(db).values("name").annotate(n=Count("name"))
    }
    late = Demand.objects.using(db).filter(delay__gt="0 seconds").count()
    return {"problems": problems, "late_demands": late}


def _ancestors(db, name):
    """The machine and its owner chain - group, section."""
    nodes = {
        r["name"]: r["owner_id"]
        for r in Resource.objects.using(db).values("name", "owner_id")
    }
    out, cur = [], name
    while cur and cur not in out:
        out.append(cur)
        cur = nodes.get(cur)
    return out


def _capable(db, operation_name, target, when):
    """
    Can this machine do this operation? True when any operationresource
    row of the operation - live at `when` - names the machine itself or
    anything up its owner chain (operations point at the GROUP, the
    engine picks the machine inside it).
    """
    if not operation_name:
        return True  # nothing to check against
    rows = [
        r
        for r in OperationResource.objects.using(db)
        .filter(operation_id=operation_name)
        .values("resource_id", "effective_start", "effective_end")
        if (r["effective_start"] is None or r["effective_start"] <= when)
        and (r["effective_end"] is None or r["effective_end"] >= when)
    ]
    if not rows:
        return True  # unconstrained operation
    chain = set(_ancestors(db, target))
    return any(r["resource_id"] in chain for r in rows)


def _working(db, resource_name, dt):
    """
    Is `dt` inside the machine's shift calendar? frePPLe semantics: among
    the buckets covering the instant, the LOWEST priority value wins;
    working means its value > 0. No calendar = always available.
    """
    res = (
        Resource.objects.using(db)
        .filter(name=resource_name)
        .values("available_id")
        .first()
    )
    cal = res and res["available_id"]
    if not cal:
        return True

    weekday = dt.weekday()  # 0 = Monday, matching the bucket columns
    flags = (
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday",
    )
    best = None
    for b in (
        CalendarBucket.objects.using(db)
        .filter(calendar_id=cal)
        .values(
            "startdate", "enddate", "value", "priority",
            "starttime", "endtime", *flags,
        )
    ):
        if b["startdate"] and dt < b["startdate"]:
            continue
        if b["enddate"] and dt >= b["enddate"]:
            continue
        if not b[flags[weekday]]:
            continue
        t = dt.time()
        if b["starttime"] and t < b["starttime"]:
            continue
        if b["endtime"] and t >= b["endtime"]:
            continue
        if best is None or (b["priority"] or 0) < (best["priority"] or 0):
            best = b
    if best is not None:
        return float(best["value"] or 0) > 0
    dflt = (
        Calendar.objects.using(db).filter(name=cal).values("defaultvalue").first()
    )
    return float((dflt or {}).get("defaultvalue") or 0) > 0


def _job_demand(db, op):
    """The customer order behind a bar: FK, parent FK, then pegging."""
    if op.demand_id:
        return op.demand_id
    peg = _pegging(op.plan)
    if peg:
        return max(peg.items(), key=lambda kv: kv[1])[0]
    if op.owner_id:
        parent = (
            OperationPlan.objects.using(db)
            .filter(reference=op.owner_id)
            .values("demand_id", "plan")
            .first()
        )
        if parent:
            if parent["demand_id"]:
                return parent["demand_id"]
            peg = _pegging(parent["plan"])
            if peg:
                return max(peg.items(), key=lambda kv: kv[1])[0]
    return None


def _load_movable(db, ref):
    """The operationplan, or an error response explaining why not."""
    op = OperationPlan.objects.using(db).filter(reference=ref).first()
    if not op:
        return None, _err("unknown_ref", 404)
    if op.type not in MOVABLE_TYPE or (op.status or "proposed") not in MOVABLE_STATUS:
        return None, _err("not_movable", 409, status_now=op.status, type=op.type)
    if op.startdate is None or op.enddate is None:
        return None, _err("not_movable", 409, status_now=op.status, type=op.type)
    return op, None


# ---------------------------------------------------------------- views


@login_required(login_url="/data/login/")
@require_POST
def move_validate(request):
    db = _db(request)
    if not request.user.has_perm("input.change_manufacturingorder"):
        return _err("forbidden", 403)
    payload = _payload(request)
    if payload is None:
        return _err("bad_request", 400)

    running = _running_replan(db)
    if running:
        return _err("replan_running", 409, taskid=running.id)

    op, fail = _load_movable(db, payload.get("ref"))
    if fail:
        return fail
    target = (payload.get("resource") or "").strip()
    if not Resource.objects.using(db).filter(name=target).exists():
        return _err("unknown_resource", 404)
    new_start = _parse_dt(payload.get("start"))
    if not new_start:
        return _err("bad_request", 400)

    # Snap, keep the duration - the engine will recompute the true length
    # on the new machine after the replan anyway.
    new_start = datetime.fromtimestamp(
        round(new_start.timestamp() / SNAP_SECONDS) * SNAP_SECONDS
    )
    duration = op.enddate - op.startdate
    new_end = new_start + duration

    old_res = (
        OperationPlanResource.objects.using(db)
        .filter(operationplan_id=op.reference)
        .values_list("resource_id", flat=True)
        .first()
    )

    now = _plan_now(db)
    concerns = []

    # -- other work already on the target machine in that slot
    family = {op.reference, op.owner_id} | set(
        OperationPlan.objects.using(db)
        .filter(owner_id=op.reference)
        .values_list("reference", flat=True)
    )
    overlaps = list(
        OperationPlanResource.objects.using(db)
        .filter(
            resource_id=target,
            operationplan__startdate__lt=new_end,
            operationplan__enddate__gt=new_start,
        )
        .exclude(operationplan_id__in=[f for f in family if f])
        .values(
            "operationplan_id",
            "operationplan__item__name",
            "operationplan__startdate",
            "operationplan__enddate",
            "operationplan__status",
        )[:6]
    )
    if overlaps:
        concerns.append(
            {
                "code": "overlap",
                "severity": "warn",
                "message": "Overlaps %d job%s already on %s - the replan will queue them around the pin"
                % (len(overlaps), "s" if len(overlaps) != 1 else "", target),
                "detail": [
                    {
                        "ref": o["operationplan_id"],
                        "item": o["operationplan__item__name"] or "",
                        "start": _fmt(o["operationplan__startdate"]),
                        "end": _fmt(o["operationplan__enddate"]),
                        "status": o["operationplan__status"] or "",
                    }
                    for o in overlaps
                ],
            }
        )

    # -- shift calendar
    if not _working(db, target, new_start):
        concerns.append(
            {
                "code": "offshift",
                "severity": "warn",
                "message": "%s is outside the shift calendar of %s"
                % (_fmt(new_start), target),
            }
        )

    # -- starting in the past / before the plan date. The LN extract has
    # no release date, so the plan date is the closest honest meaning.
    if new_start < now:
        concerns.append(
            {
                "code": "before_release",
                "severity": "warn",
                "message": "Starts before the plan date (%s)" % _fmt(now),
            }
        )

    # -- the customer order's due date
    demand_name = _job_demand(db, op)
    if demand_name:
        due = (
            Demand.objects.using(db)
            .filter(name=demand_name)
            .values_list("due", flat=True)
            .first()
        )
        if due and new_end > due:
            days = round((new_end - due).total_seconds() / 86400.0, 1)
            concerns.append(
                {
                    "code": "past_due",
                    "severity": "warn",
                    "message": "Ends %.1f days after order %s is due (%s)"
                    % (days, demand_name, _fmt(due)),
                }
            )

    # -- can the machine even do this operation?
    if not _capable(db, op.operation_id, target, new_start):
        concerns.append(
            {
                "code": "not_capable",
                "severity": "error",
                "message": "%s is not among the machines routing step %s may run on"
                % (target, op.operation_id or op.reference),
            }
        )

    # -- efficiency drift: duration is preserved on the ghost, the engine
    # will stretch or shrink it after the replan.
    if old_res and old_res != target:
        eff = {
            r["name"]: float(r["efficiency"] or 100)
            for r in Resource.objects.using(db)
            .filter(name__in=(old_res, target))
            .values("name", "efficiency")
        }
        if eff.get(old_res) != eff.get(target):
            concerns.append(
                {
                    "code": "efficiency",
                    "severity": "info",
                    "message": "%s runs at %.0f%% vs %.0f%% on %s - the true duration will differ after the replan"
                    % (target, eff.get(target, 100), eff.get(old_res, 100), old_res),
                }
            )

    return JsonResponse(
        {
            "ok": True,
            "proposed": {
                "ref": op.reference,
                "label": op.item_id or op.operation_id or op.reference,
                "from_resource": old_res or "",
                "resource": target,
                "start": _iso(new_start),
                "end": _iso(new_end),
                "start_txt": _fmt(new_start),
                "end_txt": _fmt(new_end),
                "hours": round(duration.total_seconds() / 3600.0, 2),
                "old_start_txt": _fmt(op.startdate),
            },
            "concerns": concerns,
        }
    )


@login_required(login_url="/data/login/")
@require_POST
def move_apply(request):
    db = _db(request)
    if not (
        request.user.has_perm("input.change_manufacturingorder")
        and request.user.has_perm("auth.generate_plan")
    ):
        return _err("forbidden", 403)
    payload = _payload(request)
    if payload is None:
        return _err("bad_request", 400)

    running = _running_replan(db)
    if running:
        return _err("replan_running", 409, taskid=running.id)

    snap = request.session.get(SESSION_KEY)
    if snap and snap.get("state") == "replanning":
        return _err("pending_move", 409)

    op, fail = _load_movable(db, payload.get("ref"))
    if fail:
        return fail
    target = (payload.get("resource") or "").strip()
    if not Resource.objects.using(db).filter(name=target).exists():
        return _err("unknown_resource", 404)
    new_start = _parse_dt(payload.get("start"))
    if not new_start:
        return _err("bad_request", 400)
    new_start = datetime.fromtimestamp(
        round(new_start.timestamp() / SNAP_SECONDS) * SNAP_SECONDS
    )
    new_end = new_start + (op.enddate - op.startdate)

    old_res = (
        OperationPlanResource.objects.using(db)
        .filter(operationplan_id=op.reference)
        .values_list("resource_id", flat=True)
        .first()
    )

    parent = None
    if op.owner_id:
        parent = (
            OperationPlan.objects.using(db).filter(reference=op.owner_id).first()
        )

    undo_id = "mv-%s-%s" % (
        timezone.now().strftime("%Y%m%d-%H%M%S"),
        op.reference[-8:],
    )
    before = _snapshot_metrics(db)

    with transaction.atomic(using=db):
        # The pin. queryset.update() on purpose - see the module header.
        OperationPlan.objects.using(db).filter(reference=op.reference).update(
            startdate=new_start, enddate=new_end, status="confirmed"
        )
        if old_res != target:
            OperationPlanResource.objects.using(db).filter(
                operationplan_id=op.reference
            ).update(resource_id=target)
        if parent:
            # Confirm the parent run so the replan cannot orphan the pin,
            # widening its envelope if the step now pokes past it.
            OperationPlan.objects.using(db).filter(reference=parent.reference).update(
                status="confirmed",
                startdate=min(parent.startdate or new_start, new_start),
                enddate=max(parent.enddate or new_end, new_end),
            )

    taskid = _launch_replan(db, request.user)

    request.session[SESSION_KEY] = {
        "undo_id": undo_id,
        "db": db,
        "state": "replanning",
        "taskid": taskid,
        "move": {
            "ref": op.reference,
            "label": op.item_id or op.operation_id or op.reference,
            "old": {
                "start": _iso(op.startdate),
                "end": _iso(op.enddate),
                "status": op.status or "proposed",
                "resource": old_res or "",
                "owner_ref": parent.reference if parent else None,
                "owner_status": (parent.status or "proposed") if parent else None,
                "owner_start": _iso(parent.startdate) if parent else None,
                "owner_end": _iso(parent.enddate) if parent else None,
            },
            "new": {
                "start": _iso(new_start),
                "end": _iso(new_end),
                "resource": target,
            },
        },
        "before": before,
    }
    request.session.modified = True

    return JsonResponse({"ok": True, "undo_id": undo_id, "taskid": taskid})


@login_required(login_url="/data/login/")
@require_POST
def move_undo(request):
    db = _db(request)
    if not (
        request.user.has_perm("input.change_manufacturingorder")
        and request.user.has_perm("auth.generate_plan")
    ):
        return _err("forbidden", 403)
    payload = _payload(request) or {}

    snap = request.session.get(SESSION_KEY)
    if not snap or snap.get("undo_id") != payload.get("undo_id"):
        return _err("gone", 410)
    if snap.get("db") != db:
        return _err("wrong_scenario", 409)
    if snap.get("state") not in ("done", "failed"):
        return _err("pending_move", 409)
    running = _running_replan(db)
    if running:
        return _err("replan_running", 409, taskid=running.id)

    move = snap["move"]
    old = move["old"]
    with transaction.atomic(using=db):
        OperationPlan.objects.using(db).filter(reference=move["ref"]).update(
            startdate=_parse_dt(old["start"]),
            enddate=_parse_dt(old["end"]),
            status=old["status"],
        )
        if old["resource"] and old["resource"] != move["new"]["resource"]:
            OperationPlanResource.objects.using(db).filter(
                operationplan_id=move["ref"]
            ).update(resource_id=old["resource"])
        if old.get("owner_ref"):
            OperationPlan.objects.using(db).filter(
                reference=old["owner_ref"]
            ).update(
                status=old["owner_status"],
                startdate=_parse_dt(old["owner_start"]),
                enddate=_parse_dt(old["owner_end"]),
            )

    taskid = _launch_replan(db, request.user)
    snap["state"] = "undoing"
    snap["taskid"] = taskid
    request.session[SESSION_KEY] = snap
    request.session.modified = True
    return JsonResponse({"ok": True, "taskid": taskid})


@login_required(login_url="/data/login/")
@require_POST
def move_dismiss(request):
    request.session.pop(SESSION_KEY, None)
    request.session.modified = True
    return JsonResponse({"ok": True})


@login_required(login_url="/data/login/")
def replan_status(request):
    db = _db(request)
    try:
        taskid = int(request.GET.get("id", ""))
    except (TypeError, ValueError):
        return _err("bad_request", 400)
    t = (
        Task.objects.using(db)
        .filter(id=taskid)
        .values("id", "status", "finished", "message")
        .first()
    )
    if not t:
        return _err("unknown_task", 404)
    return JsonResponse(
        {
            "taskid": t["id"],
            "status": t["status"],
            "finished": _iso(t["finished"]),
            "message": t["message"] or "",
        }
    )


# ------------------------------------------------- board banner context


def banner_context(request, db):
    """
    What the schedule view needs to render the move banner: the state of
    the last move, and - once its replan is Done - the before/after
    delta. Called on every board load; cheap when there is no move.
    """
    snap = request.session.get(SESSION_KEY)
    if not snap or snap.get("db") != db or snap.get("state") == "dismissed":
        return {"move_banner": None}

    task = (
        Task.objects.using(db)
        .filter(id=snap.get("taskid"))
        .values("id", "status", "finished")
        .first()
    )
    state = snap.get("state")
    status = (task or {}).get("status") or ""

    if task and status not in ("Done", "Failed", "Canceled"):
        mode = "replanning"
    elif state == "replanning" and status == "Done":
        # The replan just finished: take the after-metrics once and keep
        # them in the session so the banner survives further reloads.
        snap["after"] = _snapshot_metrics(db)
        snap["state"] = state = "done"
        request.session[SESSION_KEY] = snap
        request.session.modified = True
        mode = "done"
    elif state == "undoing" and status == "Done":
        snap["state"] = state = "undone"
        request.session[SESSION_KEY] = snap
        request.session.modified = True
        mode = "undone"
    elif status in ("Failed", "Canceled"):
        if state in ("replanning", "undoing"):
            snap["state"] = "failed"
            request.session[SESSION_KEY] = snap
            request.session.modified = True
        mode = "failed"
    else:
        mode = state  # done / undone / failed on a later reload

    delta = None
    if mode == "done" and snap.get("after"):
        b, a = snap["before"], snap["after"]
        names = sorted(set(b["problems"]) | set(a["problems"]))
        delta = {
            "late_before": b["late_demands"],
            "late_after": a["late_demands"],
            "problems": [
                {
                    "name": n,
                    "before": b["problems"].get(n, 0),
                    "after": a["problems"].get(n, 0),
                }
                for n in names
                if b["problems"].get(n, 0) != a["problems"].get(n, 0)
            ],
        }

    return {
        "move_banner": {
            "mode": mode,
            "move": snap["move"],
            "undo_id": snap["undo_id"],
            "taskid": snap.get("taskid"),
            "task_status": status,
            "delta": delta,
        }
    }
