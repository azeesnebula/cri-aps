"""
The Constraints screen - where the planner tells the APS what the
bottleneck is, and runs the plan around it.

    GET  /constraints/          the form, the candidates, the last result
    POST /constraints/save      store the drum and its rate
    POST /constraints/run       exploit the drum, freeze it, replan
    GET  /constraints/status/   poll the replan

WHY THIS SCREEN EXISTS
----------------------
Theory of Constraints needs exactly one thing the system cannot work out
for itself: which resource is the bottleneck. Sieger's planners know it -
they will tell you fabrication does 50 a day - and until this screen there
was nowhere for them to say so. It lived in a CSV on a developer's laptop,
which meant a planner had to phone someone to run TOC. That is not a
workflow.

WHERE THE ANSWER IS KEPT
------------------------
frePPLe's own parameter table, so it travels with the database, survives a
redeploy, appears in Admin -> Parameters, and is per-scenario like every
other setting. No new table, which keeps this app's no-migrations deploy.

THE THREE STEPS, AND WHY THE PARENT MO IS CONFIRMED TOO
-------------------------------------------------------
  1. EXPLOIT      re-lay the drum's jobs end to end on its shift calendar,
                  in due-date order, capped at its declared rate.
  2. FREEZE       set them 'confirmed', WITH their parent MOs.
  3. SUBORDINATE  replan; the engine arranges everything else around them.

Step 2 confirms the parent on purpose, and it is not optional: runplan
NULLs the owner of any child whose parent is still proposed before deleting
(output/commands.py), so a confirmed step under a proposed parent is
orphaned and its routing regenerated beside it. The first version of this
froze only the children, and the replan churned at 99% CPU for an hour
without converging. moves.py learned the same lesson for the drag-and-drop
pin; this follows it.

Both writes use queryset.update() deliberately: model save() would cascade
the status to every sibling step through propagateStatus, and the siblings
must stay proposed so the engine is free to re-place them around the drum.
"""

import json
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db import connections
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from freppledb.common.models import Parameter
from freppledb.execute.models import Task
from freppledb.input.models import OperationPlan, Resource

from .toc_pack import pack, slots

# Same arguments the execute screen uses, so a TOC replan is the same kind
# of plan as any other - only the frozen drum makes it different.
RUNPLAN_ARGS = "--constraint=capa,mfg_lt,po_lt --plantype=1 --env=supply"

P_DRUM = "toc.drum"
P_RATE = "toc.rate_per_day"
P_BUFFER = "toc.buffer_days"

HORIZON_WEEKS = 8


def _db(request):
    return getattr(request, "database", "default") or "default"


def _param(db, name, default=""):
    try:
        return Parameter.objects.using(db).get(name=name).value or default
    except Parameter.DoesNotExist:
        return default


def _set_param(db, name, value, description):
    Parameter.objects.using(db).update_or_create(
        name=name, defaults={"value": value, "description": description})


def _rows(db, sql, params=None):
    with connections[db].cursor() as c:
        c.execute(sql, params or [])
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


# ----------------------------------------------------- more than one drum
#
# Theory of Constraints says a system has ONE constraint, and that is about
# where a manager points their attention - it is not a technical limit.
# This plan has three groups at their ceiling at once (MIG welding 400A
# needing 1.1x what it has, textile assembly level with it, shearing at
# 99.9%), and exploiting only one leaves the other two unmanaged.
#
# Several drums are safe ONLY while they are independent. The exploit packs
# a drum's jobs end to end and FREEZES them; a job passing through two
# drums would be frozen twice, by two packs that never saw each other, and
# the two placements can contradict - step 5 pinned before step 3 finishes.
# Nothing downstream would notice, because both look like ordinary
# confirmed operationplans.
#
# So overlap is checked before anything is written, every time, rather than
# assumed from today's data. On the September plan no job touches two of
# the three candidates - but that is a fact about this extract, not a
# property of the model, and a different routing set would break it.

OVERLAP_SQL = """
select op.owner_id as mo,
       string_agg(distinct coalesce(r.owner_id, opr.resource_id), ', ') as drums
from operationplanresource opr
join operationplan op on op.reference = opr.operationplan_id
left join resource r on r.name = opr.resource_id
where op.type = 'WO'
  and op.owner_id is not null
  and coalesce(r.owner_id, opr.resource_id) = any(%s)
group by 1
having count(distinct coalesce(r.owner_id, opr.resource_id)) > 1
limit 5
"""


def _split(raw):
    """A comma separated parameter into a clean list, order preserved."""
    out = []
    for part in (raw or "").split(","):
        p = part.strip()
        if p and p not in out:
            out.append(p)
    return out


def _drums(db):
    return _split(_param(db, P_DRUM))


def _rate_map(db):
    """
    Rates line up with drums by position: 'HFMG1,SMSH1' + '50,' means 50 a
    day on the first and no cap on the second. A blank is a real answer -
    'I do not know the rate' - and must stay distinguishable from zero.
    """
    drums = _drums(db)
    raw = [r.strip() for r in (_param(db, P_RATE) or "").split(",")]
    out = {}
    for i, d in enumerate(drums):
        v = raw[i] if i < len(raw) else ""
        try:
            out[d] = float(v) if v else None
        except ValueError:
            out[d] = None
    return out


def _overlap(db, drums):
    """Jobs that pass through more than one of the chosen drums."""
    if len(drums) < 2:
        return []
    return _rows(db, OVERLAP_SQL, [drums])


# ---------------------------------------------------------------- reading


# Ranked by utilisation from the engine's own accounting, so the planner
# picks the drum from evidence instead of memory. Groups, not machines:
# a constraint is a work centre.
CANDIDATES_SQL = """
select coalesce(r.owner_id, r.name) as grp,
       coalesce(g.description, '')  as descr,
       count(distinct r.name)       as machines,
       round(sum(rp.available), 0)  as available_h,
       round(sum(rp.load + coalesce(rp.setup, 0)), 0) as busy_h,
       case when sum(rp.available) > 0
            then round(100.0 * sum(rp.load + coalesce(rp.setup,0))
                       / sum(rp.available), 1)
            else 0 end as pct
from out_resourceplan rp
join resource r on r.name = rp.resource
left join resource g on g.name = r.owner_id
where r.type = 'default'
  and rp.startdate >= (select min(startdate) from operationplan where type='WO')
  and rp.startdate <  (select min(startdate) from operationplan where type='WO')
                      + interval '%s weeks'
group by 1, 2
order by pct desc
limit 15
"""


def _candidates(db):
    """
    The candidate list, with utilisation and OVERLOAD kept apart.

    A machine cannot be 1,971% utilised. What that number really says is
    that the plan put 15,138 hours of work on a machine with 768 hours in
    it - twenty times more than exists. Printing it in a column headed
    "busy" invites a planner to read it as a utilisation, which is the one
    thing it is not.

    So anything over 100% is split in two: utilisation caps at 100 (the
    machine is full, and it cannot be fuller), and the excess is reported
    separately as a multiple - "needs 19.7x capacity". One is a
    measurement, the other is a shortfall, and they are different kinds of
    fact.
    """
    try:
        rows = _rows(db, CANDIDATES_SQL % HORIZON_WEEKS)
    except Exception:
        return []          # no plan yet - the screen still has to render
    for r in rows:
        pct = float(r.get("pct") or 0)
        r["used_pct"] = min(pct, 100.0)
        r["over"] = round(pct / 100.0, 1) if pct > 100.0 else None
    return rows


def _members(db, name):
    """A declared group expanded to the machines that carry its load."""
    res = Resource.objects.using(db).filter(name=name).first()
    if not res:
        return []
    if res.type == "default":
        return [name]
    return list(
        Resource.objects.using(db)
        .filter(Q(owner_id=name) | Q(owner__owner_id=name), type="default")
        .values_list("name", flat=True))


def _running(db):
    return (Task.objects.using(db)
            .filter(name="runplan", finished__isnull=True)
            .exclude(status__in=("Done", "Failed", "Canceled"))
            .order_by("-id").first())


# ---------------------------------------------------------------- the run


SHIFT_SQL = """
select to_char(b.starttime,'HH24:MI:SS') s, to_char(b.endtime,'HH24:MI:SS') e,
       b.sunday, b.monday, b.tuesday, b.wednesday, b.thursday, b.friday, b.saturday
from resource r join calendarbucket b on b.calendar_id = r.available_id
where r.name = %s and b.value > 0 order by b.starttime
"""

DAYS_SQL = """
select to_char(startdate,'YYYY-MM-DD') d from out_resourceplan
where resource = %s and available > 0
  and startdate >= (select min(startdate) from operationplan where type='WO')
order by startdate
"""

JOBS_SQL = """
select opr.operationplan_id as ref, op.quantity, op.owner_id,
       extract(epoch from (op.enddate - op.startdate))/3600.0 as hours,
       op.startdate as earliest,
       coalesce(d.due, op.enddate) as due
from operationplanresource opr
join operationplan op on op.reference = opr.operationplan_id
left join lateral (
  select min(dm.due) due from demand dm
  where dm.name in (select k from jsonb_object_keys(
        coalesce(op.plan->'pegging','{}'::jsonb)) k)
) d on true
where opr.resource_id = any(%s) and op.type = 'WO' and op.status = 'proposed'
order by 6, 1
"""


def _calendar(db, machines):
    cal = {}
    for m in machines:
        shifts = []
        for r in _rows(db, SHIFT_SQL, [m]):
            flags = [r["sunday"], r["monday"], r["tuesday"], r["wednesday"],
                     r["thursday"], r["friday"], r["saturday"]]
            wd = {6 if i == 0 else i - 1 for i, on in enumerate(flags) if on}
            shifts.append((r["s"], r["e"], wd))
        days = [x["d"] for x in _rows(db, DAYS_SQL, [m])]
        cal[m] = slots(days, shifts)
    return {m: s for m, s in cal.items() if s}


def _exploit(db, drum, rate):
    """Pack the drum, freeze it and its parents. Returns a summary dict."""
    machines = _members(db, drum)
    if not machines:
        return {"error": "'%s' has no machines under it, so it cannot be a "
                         "constraint." % drum}

    jobs_raw = _rows(db, JOBS_SQL, [machines])
    if not jobs_raw:
        return {"error": "No unplanned jobs on '%s'. Either the plan put no "
                         "work there, or it is already frozen." % drum}

    cal = _calendar(db, machines)
    if not cal:
        return {"error": "'%s' has no working hours - it has no shift "
                         "calendar, so it cannot be a constraint." % drum}

    jobs = [(j["ref"], float(j["quantity"] or 0), float(j["hours"] or 0),
             j["earliest"]) for j in jobs_raw]
    placed, starved = pack(jobs, cal, rate)
    if not placed:
        return {"error": "Nothing could be placed on '%s' within the "
                         "horizon." % drum}

    owners = {j["ref"]: j["owner_id"] for j in jobs_raw}
    refs = [p[0] for p in placed]

    # The drum's own jobs: exact dates, frozen.
    for ref, _mc, start, end in placed:
        (OperationPlan.objects.using(db).filter(reference=ref)
         .update(startdate=start, enddate=end, status="confirmed"))

    # Their parents, frozen too - see the module docstring. update(), not
    # save(), so propagateStatus cannot cascade to the sibling steps.
    parents = {owners[r] for r in refs if owners.get(r)}
    if parents:
        (OperationPlan.objects.using(db)
         .filter(reference__in=parents).update(status="confirmed"))

    return {"machines": machines, "jobs": len(jobs), "placed": len(placed),
            "starved": starved, "parents": len(parents),
            "unplaced": len(jobs) - len(placed)}


# ----------------------------------------------------------------- views


@login_required(login_url="/data/login/")
def constraints(request):
    db = _db(request)
    drums = _drums(db)
    rates = _rate_map(db)
    running = _running(db)

    # Mark the chosen ones on the candidate table so the checkboxes come
    # back ticked, and carry each one's rate into its own input.
    cands = _candidates(db)
    for c in cands:
        c["chosen"] = c["grp"] in drums
        r = rates.get(c["grp"])
        c["rate"] = ("%g" % r) if r is not None else ""

    return render(request, "sieger_ui/constraints.html", {
        "title": _("constraints"),
        "drum": ",".join(drums),
        "drums": drums,
        "rate": _param(db, P_RATE),
        "buffer": _param(db, P_BUFFER, "2"),
        "candidates": cands,
        "members": sum((_members(db, d) for d in drums), []),
        "running": running,
        "overlap": _overlap(db, drums),
        "last": json.loads(_param(db, "toc.last_result", "null") or "null"),
    })


@require_POST
@login_required(login_url="/data/login/")
def save(request):
    db = _db(request)
    drum = (request.POST.get("drum") or "").strip()
    rate = (request.POST.get("rate") or "").strip()
    buf = (request.POST.get("buffer") or "2").strip()

    drums = _split(drum)
    for d in drums:
        if not Resource.objects.using(db).filter(name=d).exists():
            return JsonResponse({"error": "No resource called '%s'." % d},
                                status=400)
    for r in (rate or "").split(","):
        if r.strip():
            try:
                float(r.strip())
            except ValueError:
                return JsonResponse(
                    {"error": "Rate must be a number - '%s' is not."
                              % r.strip()}, status=400)

    _set_param(db, P_DRUM, ",".join(drums),
               "TOC: the constraint resources (the drums), comma separated")
    _set_param(db, P_RATE, rate,
               "TOC: what each constraint produces per day, in drum order")
    _set_param(db, P_BUFFER, buf, "TOC: days of buffer held before the constraint")

    # Saving a clashing pair is allowed - the planner may be mid-edit - but
    # they are told now rather than at Run, when it is a surprise.
    clash = _overlap(db, drums)
    return JsonResponse({
        "ok": True,
        "drum": ",".join(drums),
        "rate": rate,
        "members": sum((_members(db, d) for d in drums), []),
        "warning": (
            "%s share work with each other, so they cannot be exploited "
            "together. Run will refuse until you drop one."
            % " and ".join(sorted({x for c in clash
                                   for x in c["drums"].split(", ")}))
        ) if clash else "",
    })


@require_POST
@login_required(login_url="/data/login/")
def run(request):
    db = _db(request)
    drums = _drums(db)
    if not drums:
        return JsonResponse(
            {"error": "No constraint set. Choose the machine or work centre "
                      "that limits your output, then press Run."}, status=400)
    if _running(db):
        return JsonResponse(
            {"error": "A plan is already running. Wait for it to finish - two "
                      "at once will deadlock."}, status=409)

    # Refuse BEFORE writing anything. A job frozen by two packs that never
    # saw each other can end up with step 5 pinned before step 3 finishes,
    # and nothing downstream would catch it.
    clash = _overlap(db, drums)
    if clash:
        return JsonResponse({
            "error": (
                "These constraints share work, so they cannot be exploited "
                "together: %s. Each one re-lays its own jobs and freezes "
                "them, and a job on two drums would be frozen twice by two "
                "packs that never saw each other - which can pin a later "
                "step before an earlier one finishes. Run them one at a "
                "time, or pick constraints that do not share jobs."
            ) % "; ".join(
                "job %s passes through %s" % (c["mo"], c["drums"])
                for c in clash
            ),
            "overlap": clash,
        }, status=400)

    rates = _rate_map(db)
    per_drum, placed_total, failed = [], 0, []
    for d in drums:
        r = _exploit(db, d, rates.get(d))
        if "error" in r:
            # One unusable drum must not sink the rest - report it and
            # carry on, so the planner sees which of the three worked.
            failed.append({"drum": d, "error": r["error"]})
            continue
        r["drum"] = d
        per_drum.append(r)
        placed_total += r["placed"]

    if not per_drum:
        return JsonResponse(
            {"error": "; ".join(f["error"] for f in failed)}, status=400)

    # ONE replan for all of them. Three drums do not need three solves, and
    # three would each undo the last one's subordination.
    task = Task(name="runplan", submitted=timezone.now(), status="Waiting",
                user=request.user, arguments=RUNPLAN_ARGS)
    task.save(using=db)
    from freppledb.execute.management.commands.runworker import launchWorker
    launchWorker(database=db)

    result = {
        "taskid": task.id,
        "at": datetime.now().strftime("%d %b %Y, %H:%M"),
        "drum": ", ".join(p["drum"] for p in per_drum),
        "drums": per_drum,
        "failed": failed,
        "placed": placed_total,
        "jobs": sum(p["jobs"] for p in per_drum),
        "parents": sum(p["parents"] for p in per_drum),
    }
    _set_param(db, "toc.last_result", json.dumps(result),
               "TOC: what the last constraint-first run did")
    return JsonResponse(result)


@login_required(login_url="/data/login/")
def status(request):
    db = _db(request)
    t = (Task.objects.using(db).filter(name="runplan")
         .order_by("-id").values("id", "status", "finished").first())
    if not t:
        return JsonResponse({"status": "none"})
    return JsonResponse({
        "status": t["status"] or "",
        "done": bool(t["finished"]) or t["status"] in ("Done", "Failed", "Canceled"),
    })
