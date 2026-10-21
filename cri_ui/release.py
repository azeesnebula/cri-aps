#
# The pre-release checkpoint panel.
#
# Sieger releases a rolling window (3 days by default) to LN and the
# shop floor. Before that button may be pressed, the plan has to be
# CLEAN OR CONSCIOUSLY ACCEPTED: late projects, capacity overloads and
# hot machines must each be either empty or explicitly acknowledged by
# a planner. That is the customer's stated process - "all these
# resolutions have to be done, once accepted the planner pushes it".
#
# Release itself flips every proposed manufacturing order starting
# inside the window - and its routing steps - to 'approved'. That IS
# the freeze: a full replan deletes only proposed operationplans
# (freppledb/output/commands.py, the runplan delete), so approved work
# survives the next run untouched. The LN export stays the existing
# manual step (loader/export_schedule.py); this screen records WHO
# released WHAT and WHEN.
#
# Acknowledgements and the release audit are Comment rows attached to
# the LATEST runplan Task. That placement is the point:
#   - a new replan produces a new Task id, so every ack automatically
#     expires with the plan it was given against;
#   - the history survives forever in frePPLe's own comment trail;
#   - no new table, so the app keeps its no-migrations deploy.
#

import json

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from freppledb.common.models import Comment
from freppledb.execute.models import Task
from freppledb.input.models import Demand, Operation, OperationPlan
from freppledb.output.models import Problem

from .views import _days, _num, _plan_now

# The window control. 3 days is the agreed release cadence; the rest are
# for looking ahead.
WINDOW_CHOICES = (1, 2, 3, 5, 7)
MAX_WINDOW = 14
DEFAULT_UTIL_THRESHOLD = 90

ACKABLE = ("late", "overload", "util")

ACK_PREFIX = "release-ack:"
AUDIT_PREFIX = "release-done:"


# ----------------------------------------------------------------- shared


def _db(request):
    return getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS


def _last_runplan(db):
    return (
        Task.objects.using(db)
        .filter(name="runplan")
        .order_by("-id")
        .first()
    )


def _task_ct(db):
    return ContentType.objects.db_manager(db).get_for_model(Task)


def _acks(db, task):
    """{checkpoint: {"user": ..., "at": ...}} for the current plan."""
    if not task:
        return {}
    out = {}
    for c in (
        Comment.objects.using(db)
        .filter(
            content_type=_task_ct(db),
            object_pk=str(task.id),
            comment__startswith=ACK_PREFIX,
        )
        .select_related("user")
        .order_by("lastmodified")
    ):
        parts = c.comment.split(":", 2)
        if len(parts) >= 2 and parts[1] in ACKABLE:
            out[parts[1]] = {
                "user": c.user.username if c.user else "",
                "at": c.lastmodified,
            }
    return out


def _window(request, now):
    try:
        days = int(request.GET.get("days", 3))
    except (TypeError, ValueError):
        days = 3
    days = max(1, min(days, MAX_WINDOW))
    return days, now + timedelta(days=days)


def _util_threshold(request):
    try:
        pct = int(request.GET.get("util", DEFAULT_UTIL_THRESHOLD))
    except (TypeError, ValueError):
        pct = DEFAULT_UTIL_THRESHOLD
    return max(50, min(pct, 100))


def _proposed_qs(db, window_end):
    """The MOs a release would flip - the panel's headline number."""
    return OperationPlan.objects.using(db).filter(
        type="MO", status="proposed", startdate__lt=window_end
    )


# ------------------------------------------------------------- the panel


@login_required(login_url="/data/login/")
def panel(request):
    db = _db(request)
    now = _plan_now(db)
    days, window_end = _window(request, now)
    util_pct = _util_threshold(request)
    prefix = getattr(request, "prefix", "") or ""

    task = _last_runplan(db)
    plan_ready = bool(task and task.status == "Done")
    acks = _acks(db, task)

    # -- checkpoint 1: late projects --------------------------------------
    late_rows = list(
        Demand.objects.using(db)
        .filter(delay__gt="0 seconds")
        .order_by("-delay")
        .values("name", "item_id", "due", "deliverydate", "delay", "priority")[:50]
    )
    for r in late_rows:
        r["delay_days"] = _days(r["delay"])
    late_count = Demand.objects.using(db).filter(delay__gt="0 seconds").count()

    # -- checkpoint 2: capacity overloads in the window --------------------
    overload_qs = Problem.objects.using(db).filter(
        name="overload", startdate__lt=window_end, enddate__gt=now
    )
    overload_rows = list(
        overload_qs.order_by("startdate").values(
            "owner", "description", "startdate", "enddate"
        )[:50]
    )
    overload_count = overload_qs.count()

    # -- checkpoint 3: orders that cannot be planned at all ----------------
    # Same test as the cockpit's step 1 - informational here, because a
    # release cannot touch an order that has no operations.
    routed = (
        Operation.objects.using(db)
        .filter(type="routing", item__isnull=False)
        .values_list("item_id", flat=True)
    )
    blocked_qs = Demand.objects.using(db).exclude(item_id__in=routed)
    blocked_rows = list(blocked_qs.order_by("due").values("name", "item_id", "due")[:50])
    blocked_count = blocked_qs.count()

    # -- checkpoint 4: what is in the window, by status ---------------------
    window_split = {
        row["status"] or "?": row["n"]
        for row in OperationPlan.objects.using(db)
        .filter(type="MO", startdate__lt=window_end)
        .exclude(status__in=("closed", "completed"))
        .values("status")
        .annotate(n=Count("reference"))
    }
    proposed_count = _proposed_qs(db, window_end).count()
    window_jobs = list(
        _proposed_qs(db, window_end)
        .order_by("startdate")
        .values("reference", "operation_id", "item_id", "startdate", "enddate", "quantity")[:200]
    )
    for j in window_jobs:
        j["quantity"] = _num(j["quantity"])

    # -- checkpoint 5: machines loaded past the threshold -------------------
    # out_resourceplan holds HOURS per day bucket - not seconds. frePPLe's
    # own resource report divides by 1.0 for "hours", 24 for days, 168 for
    # weeks (output/views/resource.py:265-278), and the raw rows agree: a
    # two-shift day reads available=16, unavailable=8. This used to divide
    # by 3600; the util % was unharmed (a ratio cancels the factor) but the
    # busy/available hours in the table were 3600x too small.
    #
    # Utilisation is (load+setup)/available, the same arithmetic as
    # frePPLe's own resource report row average, and as /bottlenecks/.
    #
    # The join on resource.type='default' matters: out_resourceplan also
    # carries rows for the SECTIONS and GROUPS (loaded as 'infinite'), and
    # a group's numbers are just its machines' summed again - so without
    # this filter a one-machine group like SMLS1 appeared in the hot list
    # twice, once under its own name and once under the machine's, and
    # the "N machines above the threshold" count was inflated (13 of 35
    # entries were groups, not machines).
    with connections[db].cursor() as c:
        c.execute(
            """
            select rp.resource,
                   sum(coalesce(rp.load,0)+coalesce(rp.setup,0))  as busy_h,
                   sum(coalesce(rp.available,0))                  as avail_h,
                   100.0*sum(coalesce(rp.load,0)+coalesce(rp.setup,0))
                        /nullif(sum(coalesce(rp.available,0)),0)   as util
            from out_resourceplan rp
            join resource r on r.name = rp.resource
            where r.type = 'default'
              and rp.startdate >= %s and rp.startdate < %s
            group by rp.resource
            having sum(coalesce(rp.available,0)) > 0
               and sum(coalesce(rp.load,0)+coalesce(rp.setup,0))
                   > %s * sum(coalesce(rp.available,0)) / 100.0
            order by util desc
            """,
            [now, window_end, util_pct],
        )
        hot_rows = [
            {
                "resource": r[0],
                "busy_h": round(float(r[1] or 0), 1),
                "avail_h": round(float(r[2] or 0), 1),
                "util": round(float(r[3] or 0), 1),
            }
            for r in c.fetchall()
        ]
    hot_count = len(hot_rows)

    checkpoints = [
        {
            "key": "late",
            "title": _("late projects"),
            "count": late_count,
            "ackable": True,
            "ack": acks.get("late"),
            "rows": late_rows,
            "links": (
                (prefix + "/", _("shipment summary")),
                (prefix + "/constraint/", _("why late")),
            ),
            "note": _(
                "Orders whose planned delivery lands after the promised date. "
                "Resolve by re-planning, re-negotiating the date, or acknowledge "
                "to release anyway."
            ),
        },
        {
            "key": "overload",
            "title": _("capacity overloads in the window"),
            "count": overload_count,
            "ackable": True,
            "ack": acks.get("overload"),
            "rows": overload_rows,
            "links": (
                (
                    prefix
                    + "/machine-schedule/?days=%d" % days,
                    _("plan board"),
                ),
                (prefix + "/problem/?noautofilter&name=overload", _("problem report")),
            ),
            "note": _(
                "The engine booked more work onto a machine than its calendar "
                "provides inside this window."
            ),
        },
        {
            "key": "blocked",
            "title": _("orders that cannot be planned"),
            "count": blocked_count,
            "ackable": False,
            "ack": None,
            "rows": blocked_rows,
            "links": (
                (prefix + "/build-graph/?show=blocked", _("blocked orders")),
                (prefix + "/data/input/operation/", _("routings")),
            ),
            "note": _(
                "No routing behind the part, so no operations exist to release. "
                "Shown so nothing disappears silently - fixing it needs the "
                "routing from LN."
            ),
        },
        {
            "key": "util",
            "title": _("machines above %d%% in the window") % util_pct,
            "count": hot_count,
            "ackable": True,
            "ack": acks.get("util"),
            "rows": hot_rows,
            "links": (
                (prefix + "/resource/", _("machine load report")),
                (prefix + "/machine-schedule/?days=%d" % days, _("plan board")),
            ),
            "note": _(
                "Loaded close to the calendar's limit - one breakdown and the "
                "window slips. Acknowledge if that risk is accepted."
            ),
        },
    ]

    # The gate: plan finished + every ackable checkpoint clear or acked.
    open_checkpoints = [
        c["key"]
        for c in checkpoints
        if c["ackable"] and c["count"] > 0 and not c["ack"]
    ]
    can_release = (
        plan_ready
        and proposed_count > 0
        and not open_checkpoints
        and request.user.has_perm("input.change_operationplan")
    )

    # The most recent release note, for the header.
    last_release = (
        Comment.objects.using(db)
        .filter(comment__startswith=AUDIT_PREFIX)
        .select_related("user")
        .order_by("-lastmodified")
        .first()
    )

    return render(
        request,
        "sieger_ui/release.html",
        context={
            "title": _("release window"),
            "now": now,
            "days": days,
            "window_end": window_end,
            "window_choices": WINDOW_CHOICES,
            "util_pct": util_pct,
            "task": task,
            "plan_ready": plan_ready,
            "checkpoints": checkpoints,
            "open_checkpoints": open_checkpoints,
            "window_split": window_split,
            "window_jobs": window_jobs,
            "proposed_count": proposed_count,
            "can_release": can_release,
            "can_ack": request.user.has_perm("input.change_operationplan"),
            "last_release": last_release,
        },
    )


# ---------------------------------------------------------------- writes


def _forbidden():
    return JsonResponse({"error": "forbidden"}, status=403)


@login_required(login_url="/data/login/")
@require_POST
def ack(request):
    """Acknowledge (or withdraw) one checkpoint against the current plan."""
    db = _db(request)
    if not request.user.has_perm("input.change_operationplan"):
        return _forbidden()
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "bad_request"}, status=400)

    checkpoint = payload.get("checkpoint")
    if checkpoint not in ACKABLE:
        return JsonResponse({"error": "unknown_checkpoint"}, status=400)

    task = _last_runplan(db)
    if not task:
        return JsonResponse({"error": "no_plan"}, status=409)

    ct = _task_ct(db)
    if payload.get("undo"):
        Comment.objects.using(db).filter(
            content_type=ct,
            object_pk=str(task.id),
            comment__startswith=ACK_PREFIX + checkpoint + ":",
            user=request.user,
        ).delete()
        return JsonResponse({"ok": True, "checkpoint": checkpoint, "acked": False})

    detail = {"count": payload.get("count"), "days": payload.get("days")}
    Comment(
        content_type=ct,
        object_pk=str(task.id),
        object_repr="runplan %s" % task.id,
        type="comment",
        comment=ACK_PREFIX + checkpoint + ":" + json.dumps(detail),
        user=request.user,
    ).save(using=db)
    return JsonResponse(
        {
            "ok": True,
            "checkpoint": checkpoint,
            "acked": True,
            "task": task.id,
            "user": request.user.username,
        }
    )


@login_required(login_url="/data/login/")
@require_POST
def release_action(request):
    """
    Flip every proposed MO starting inside the window - and its routing
    steps - to 'approved', inside one transaction.

    The two bulk updates replicate exactly what per-object save() +
    propagateStatus() (freppledb/input/models/operationplan.py) would do
    for the proposed -> approved transition: children of an approved
    routing MO follow their parent. DO NOT reuse this shortcut for any
    other transition - confirmed/completed/closed carry date-clamping
    and material logic that a bulk update would silently skip.
    """
    db = _db(request)
    if not request.user.has_perm("input.change_operationplan"):
        return _forbidden()
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "bad_request"}, status=400)

    now = _plan_now(db)
    try:
        days = int(payload.get("days", 3))
    except (TypeError, ValueError):
        days = 3
    days = max(1, min(days, MAX_WINDOW))
    window_end = now + timedelta(days=days)

    # Server-side gate - never trust a disabled button.
    task = _last_runplan(db)
    if not task or task.status != "Done":
        return JsonResponse(
            {"error": "plan_running", "status": task.status if task else None},
            status=409,
        )

    acks = _acks(db, task)
    open_cp = []
    if Demand.objects.using(db).filter(delay__gt="0 seconds").exists() and not acks.get(
        "late"
    ):
        open_cp.append("late")
    if (
        Problem.objects.using(db)
        .filter(name="overload", startdate__lt=window_end, enddate__gt=now)
        .exists()
        and not acks.get("overload")
    ):
        open_cp.append("overload")
    # The util checkpoint re-uses the panel's threshold; a threshold the
    # planner tightened via ?util= only affects the panel view, so the
    # gate holds it at the default.
    with connections[db].cursor() as c:
        c.execute(
            """
            select 1
            from out_resourceplan rp
            join resource r on r.name = rp.resource
            where r.type = 'default'          -- machines only, as above
              and rp.startdate >= %s and rp.startdate < %s
            group by rp.resource
            having sum(coalesce(rp.available,0)) > 0
               and sum(coalesce(rp.load,0)+coalesce(rp.setup,0))
                   > %s * sum(coalesce(rp.available,0)) / 100.0
            limit 1
            """,
            [now, window_end, DEFAULT_UTIL_THRESHOLD],
        )
        if c.fetchone() and not acks.get("util"):
            open_cp.append("util")
    if open_cp:
        return JsonResponse(
            {"error": "checkpoints_open", "open": open_cp}, status=409
        )

    # Staleness guard: the count the planner saw must still hold, or a
    # replan finished under them while the page sat open.
    expected = payload.get("expected")
    actual = _proposed_qs(db, window_end).count()
    if expected is not None and int(expected) != actual:
        return JsonResponse(
            {"error": "stale", "proposed": actual}, status=409
        )
    if actual == 0:
        return JsonResponse({"error": "nothing_to_release"}, status=409)

    with transaction.atomic(using=db):
        refs = list(_proposed_qs(db, window_end).values_list("reference", flat=True))
        n_wo = (
            OperationPlan.objects.using(db)
            .filter(owner_id__in=refs, status="proposed")
            .update(status="approved")
        )
        n_mo = (
            OperationPlan.objects.using(db)
            .filter(reference__in=refs, status="proposed")
            .update(status="approved")
        )
        audit = Comment(
            content_type=_task_ct(db),
            object_pk=str(task.id),
            object_repr="runplan %s" % task.id,
            type="comment",
            comment=AUDIT_PREFIX
            + json.dumps(
                {
                    "window_end": window_end.isoformat(),
                    "days": days,
                    "released_mo": n_mo,
                    "released_wo": n_wo,
                    "acks": {k: v["user"] for k, v in acks.items()},
                }
            ),
            user=request.user,
        )
        audit.save(using=db)

    return JsonResponse(
        {
            "ok": True,
            "released_mo": n_mo,
            "released_wo": n_wo,
            "window_end": window_end.isoformat(),
            "task": task.id,
            "audit_id": audit.id,
        }
    )
