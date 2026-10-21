"""
"What to do next" - the plan explains itself, and says what would help.

    GET  /improve/          the standing, the constraints, the suggestions
    POST /improve/prove/    run one suggestion as a what-if and measure it
    GET  /improve/status/   poll that run
    POST /improve/dismiss/  hide a suggestion the planner has judged

WHY THIS SCREEN EXISTS
----------------------
Every other screen reports what the plan IS. A planner reading them still
has to work out what to do about it, and that has so far happened by a
developer running scripts on the side. This screen does that reasoning in
the app, after every plan, for whatever data is loaded.

NOTHING HERE IS HARDCODED, AND THAT IS THE WHOLE POINT
------------------------------------------------------
Not a group name, not a machine count, not a date window, not a shift
pattern, not a threshold. A different extract - different plant, different
naming, different horizon - must produce its own suggestions with no code
change. Two rules keep it that way:

  * The unit of analysis is whatever routings actually BOOK, read from
    operationresource at request time. Call it a group, a work centre or a
    cell: it is simply the level the data schedules against. Bookings
    landing on a child machine are rolled up to it.

  * Every number comes from the plan database in this request. Horizon from
    the demand due dates, availability from out_resourceplan, machine counts
    from the resource tree, shift patterns from each group's own calendar
    buckets. The two genuine judgements - what counts as overloaded, what
    counts as idle - are parameters with defaults, editable per scenario.

HOW SURE IS EACH NUMBER
-----------------------
Three levels, never blurred, because getting this wrong is how a tool
starts lying to the people relying on it:

  arithmetic  capacity division - hours needed against hours available.
              Exact, and deliberately says nothing about delivery dates.
  measured    a what-if actually ran and the shipments were compared.

There is no third, predicted level. One was built and removed; see the note
above _suggest_idle_twin for what it got wrong and the bar any replacement
has to clear. Measurements are cached against a fingerprint of the plan, so
they vanish the moment the plan changes underneath them.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db import connections
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from freppledb.common.models import Parameter
from freppledb.common.utils import get_databases
from freppledb.execute.management.commands.runworker import launchWorker
from freppledb.execute.models import Task

from .moves import RUNPLAN_ARGS, _running_replan
from .reports import _db, _rows
from .views import _plan_now

# Parameter keys. Namespaced, like toc.* on the Constraints screen, so they
# sit visibly in Admin -> Parameters and travel with the scenario.
P_OVERLOAD = "improve.overload_pct"
P_IDLE = "improve.idle_pct"
P_DISMISSED = "improve.dismissed"
P_EXCLUDE = "improve.exclude_pattern"
P_MEASURED = "improve.measured"
# The one measurement in flight, if any. A state machine driven by the
# status poll, because a copy and a replan both take minutes and a Django
# view cannot block on them.
P_MEASURING = "improve.measuring"
# Which scenario slots this screen may OVERWRITE. Empty by default, and
# deliberately so: the only thing standing between "measure this" and
# "destroy the live plan" is knowing which databases are expendable, and
# nothing in the schema says which those are. An admin lists them once,
# comma separated, e.g. "scenario3,scenario4".
P_SLOTS = "improve.slots"

# Defaults for the two judgements. Over 100% of capacity is arithmetic, not
# opinion - a resource cannot do more than it has. "Idle enough to be worth
# asking about" genuinely is opinion, hence a parameter.
DEFAULT_OVERLOAD = 100
DEFAULT_IDLE = 50

# How much worse a set-aside situation has to get before the screen raises
# it again. A quarter worse is enough to mean the original judgement was
# made about a different problem.
RESURFACE_FACTOR = 1.25

# Resources the plant has taken out of service still sit in the master data,
# usually flagged in the description, and they look gloriously idle - which
# is precisely why they must never be offered as spare capacity. Suggesting
# work be routed to a machine marked "do not use" is the fastest way for a
# planner to stop trusting this screen.
#
# The wording is a site convention, not a fact, so it is a PARAMETER. The
# default covers the ordinary English of maintenance records; a site using
# its own vocabulary edits improve.exclude_pattern and nothing else.
DEFAULT_EXCLUDE = r"(?i)\b(do\s*n.?t\s*use|not\s+in\s+use|obsolete|retired|scrapped?|decommission\w*|disabled|out\s+of\s+service)\b"

# Words that carry no meaning when comparing two machine descriptions.
# Deliberately generic - units, connectors and separators, nothing drawn
# from any one customer's vocabulary.
STOPWORDS = {
    "the", "and", "for", "with", "max", "min", "mm", "cm", "m", "kg", "ton",
    "amps", "amp", "kw", "hp", "no", "nos", "type", "size", "x",
}


def _param(db, name, default=""):
    try:
        return Parameter.objects.using(db).get(name=name).value or default
    except Parameter.DoesNotExist:
        return default


def _set_param(db, name, value, description):
    Parameter.objects.using(db).update_or_create(
        name=name, defaults={"value": value, "description": description}
    )


def _num_param(db, name, default):
    try:
        return float(_param(db, name, "") or default)
    except (TypeError, ValueError):
        return default


def _json_param(db, name, default):
    try:
        return json.loads(_param(db, name, "") or "null") or default
    except ValueError:
        return default


# ======================================================================
# Facts - everything the generators reason over, read once per request
# ======================================================================

# The level routings actually schedule against. Whatever the resource tree
# looks like, a booking belongs to the resource the operation names; a
# booking that landed on a child machine rolls up to its parent.
GROUP_SQL = """
with grp as (
  select distinct resource_id as g from operationresource
),
book as (
  select case when opr.resource_id in (select g from grp) then opr.resource_id
              else coalesce(r.owner_id, opr.resource_id) end as g,
         op.quantity as qty, o.duration as setup, o.duration_per as per
  from operationplanresource opr
  join resource r on r.name = opr.resource_id
  join operationplan op on op.reference = opr.operationplan_id
  join operation o on o.name = op.operation_id
),
load as (
  select g,
         sum(extract(epoch from setup)/3600.0
             + qty * extract(epoch from per)/3600.0) as needs,
         count(*) as ops
  from book group by 1
),
-- Availability comes from the group's own rows when the engine reports
-- them, otherwise from its machines. Summing both double-counts: a pool of
-- five machines reports 2720h itself AND 544h per machine.
avail_self as (
  select rp.resource as g, sum(rp.available) as h
  from out_resourceplan rp
  where rp.resource in (select g from grp)
    and rp.startdate >= %s and rp.startdate < %s
  group by 1
),
avail_kids as (
  select r.owner_id as g, sum(rp.available) as h
  from out_resourceplan rp
  join resource r on r.name = rp.resource
  where r.owner_id in (select g from grp)
    and rp.startdate >= %s and rp.startdate < %s
  group by 1
)
select l.g as name,
       coalesce(res.description, '') as description,
       coalesce(res.owner_id, '') as section,
       res.type as rtype,
       coalesce(res.maximum, 1) as maximum,
       (select count(*) from resource c where c.owner_id = l.g) as machines,
       l.needs, l.ops,
       coalesce(a1.h, a2.h, 0) as avail
from load l
join resource res on res.name = l.g
left join avail_self a1 on a1.g = l.g
left join avail_kids a2 on a2.g = l.g
order by l.needs desc
"""

# Shipments. demand.category is the delivery-sequence tag where the extract
# carries one; where it does not, every order is its own shipment and the
# screen says so rather than inventing grouping.
SHIPMENT_SQL = """
select coalesce(nullif(category, ''), name) as tag,
       count(*) as orders,
       min(due) as due,
       max(deliverydate) as finish,
       count(*) filter (where deliverydate is null) as unplanned
from demand
group by 1
"""

# Every late shipment, with the date manufacturing actually starts on it.
#
# 'Starts' is the earliest routing step pegged to any order in the shipment -
# the engine's own pegging again, so it is the date the plan really begins
# cutting metal for this customer, not a date re-derived from the BOM.
#
# Ordered worst first, because that is the order a planner triages in.
LATE_SHIPMENT_SQL = """
with ship as (
    select coalesce(nullif(category, ''), name) as tag,
           count(*) as orders,
           min(due) as due,
           max(deliverydate) as finish,
           count(*) filter (where deliverydate is null) as unplanned
    from demand
    group by 1
),
started as (
    select coalesce(nullif(d.category, ''), d.name) as tag,
           min(op.startdate) as first_start
    from operationplan op
    cross join lateral jsonb_object_keys(
        coalesce(op.plan->'pegging', '{}'::jsonb)) as pegged(k)
    join demand d on d.name = pegged.k
    where op.type = 'WO'
    group by 1
)
select s.tag, s.orders, s.due, s.finish, s.unplanned, st.first_start
from ship s
left join started st on st.tag = s.tag
where s.finish is null or s.due is null or s.finish > s.due
order by (s.finish - s.due) desc nulls first
"""

# Which shipments does each group touch? Through the engine's own pegging,
# so it follows the real plan rather than a re-derived guess.
TOUCH_SQL = """
with grp as (select distinct resource_id as g from operationresource)
select case when opr.resource_id in (select g from grp) then opr.resource_id
            else coalesce(r.owner_id, opr.resource_id) end as g,
       count(distinct coalesce(nullif(d.category, ''), d.name)) as shipments,
       count(distinct case when d.deliverydate > d.due
                           then coalesce(nullif(d.category, ''), d.name) end) as late_shipments
from operationplanresource opr
join resource r on r.name = opr.resource_id
join operationplan op on op.reference = opr.operationplan_id
cross join lateral jsonb_object_keys(
    coalesce(op.plan->'pegging', '{}'::jsonb)) as pegged(k)
join demand d on d.name = pegged.k
group by 1
"""

# Two groups the data itself says can do the same work: they already appear
# together on the same operation. No names, no descriptions, no guessing.
SHARED_OP_SQL = """
select a.resource_id as a, b.resource_id as b, count(*) as ops
from operationresource a
join operationresource b
  on b.operation_id = a.operation_id and b.resource_id > a.resource_id
group by 1, 2
"""

CAL_SQL = """
select to_char(b.starttime, 'HH24:MI:SS') as s,
       to_char(b.endtime, 'HH24:MI:SS') as e,
       b.sunday, b.monday, b.tuesday, b.wednesday,
       b.thursday, b.friday, b.saturday
from resource r
join calendarbucket b on b.calendar_id = r.available_id
where r.name = %s and b.value > 0
order by b.starttime
"""

DAYS_SQL = """
select to_char(startdate, 'YYYY-MM-DD') as d
from out_resourceplan
where resource = %s and available > 0 and startdate >= %s and startdate < %s
order by startdate
"""

JOBS_SQL = """
with grp as (select distinct resource_id as g from operationresource)
select opr.operationplan_id as ref, op.quantity as qty,
       extract(epoch from (op.enddate - op.startdate))/3600.0 as hours,
       op.startdate as earliest, op.enddate as ends
from operationplanresource opr
join resource r on r.name = opr.resource_id
join operationplan op on op.reference = opr.operationplan_id
where (case when opr.resource_id in (select g from grp) then opr.resource_id
            else coalesce(r.owner_id, opr.resource_id) end) = %s
  and op.type = 'WO'
order by op.enddate
"""


def _days(seconds):
    return round((seconds or 0) / 86400.0, 1)


def _horizon(db):
    """
    The window the plan is judged over: from now to the last promise.

    Taken from the demand itself, never from a literal. A plan with no
    demand yet still returns a usable week so the page renders.
    """
    row = _rows(db, "select min(due) as lo, max(due) as hi, count(*) as n from demand")
    now = _plan_now(db)
    if not row or not row[0]["n"] or not row[0]["hi"]:
        return now, now + timedelta(days=7)
    hi = row[0]["hi"]
    lo = row[0]["lo"] or now
    return (min(lo, now) if lo else now), hi + timedelta(days=1)


def _excluded(pattern, *texts):
    """True when the plant has flagged this resource as out of service."""
    if not pattern:
        return False
    try:
        rx = re.compile(pattern)
    except re.error:
        # A site has mistyped its own pattern. Excluding nothing is the safe
        # failure here: a stray suggestion beats a screen that silently
        # hides half the plant.
        return False
    return any(rx.search(t or "") for t in texts)


def _tokens(text):
    """Meaningful words in a machine description, for similarity."""
    words = re.split(r"[^A-Za-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 1 and w not in STOPWORDS and not w.isdigit()}


def _fingerprint(db, facts):
    """
    Identifies THIS plan. A cached measurement is only shown while the plan
    it was taken against is still the plan on screen - otherwise the screen
    would quote a saving from a plan nobody is looking at any more.
    """
    seed = "|".join(
        f"{g['name']}:{round(g['needs'], 1)}:{round(g['avail'], 1)}"
        for g in facts["groups"][:40]
    )
    seed += f"|{facts['standing']['late']}|{facts['standing']['avg_delay']}"
    return hashlib.md5(seed.encode()).hexdigest()[:12]


def _facts(db):
    """Everything the generators reason over. One pass, no opinions."""
    start, end = _horizon(db)
    over_pct = _num_param(db, P_OVERLOAD, DEFAULT_OVERLOAD)
    idle_pct = _num_param(db, P_IDLE, DEFAULT_IDLE)

    exclude = _param(db, P_EXCLUDE, "") or DEFAULT_EXCLUDE
    groups = _rows(db, GROUP_SQL, [start, end, start, end])
    for g in groups:
        g["retired"] = _excluded(exclude, g["description"], g["name"])
        g["load"] = round(100.0 * g["needs"] / g["avail"], 0) if g["avail"] else None
        g["machines"] = int(g["machines"]) or int(g["maximum"] or 1)
        g["shipments"] = 0
        g["late_shipments"] = 0

    by_name = {g["name"]: g for g in groups}
    for t in _rows(db, TOUCH_SQL):
        if t["g"] in by_name:
            by_name[t["g"]]["shipments"] = int(t["shipments"])
            by_name[t["g"]]["late_shipments"] = int(t["late_shipments"])

    ships = _rows(db, SHIPMENT_SQL)
    late = [s for s in ships if s["finish"] and s["due"] and s["finish"] > s["due"]]
    unplanned = sum(int(s["unplanned"] or 0) for s in ships)
    delays = [(s["finish"] - s["due"]).total_seconds() / 86400.0 for s in late]
    tagged = _rows(
        db, "select count(*) as n from demand where coalesce(category,'') <> ''"
    )
    standing = {
        "shipments": len(ships),
        "late": len(late),
        "on_time": len(ships) - len(late),
        "unplanned": unplanned,
        "avg_delay": round(sum(delays) / len(delays), 1) if delays else 0.0,
        "worst_delay": round(max(delays), 1) if delays else 0.0,
        "completes": max((s["finish"] for s in ships if s["finish"]), default=None),
        # A plan with no delivery-sequence tag is not wrong, but every order
        # counting as its own shipment is a materially different reading and
        # the planner has to be told.
        "grouped": bool(tagged and tagged[0]["n"]),
    }

    overloaded = [
        g for g in groups if g["load"] is not None and g["load"] > over_pct
    ]
    overloaded.sort(key=lambda g: (-g["late_shipments"], -(g["load"] or 0)))
    # Retired resources are excluded from the IDLE list only. They stay in
    # the load table if they somehow carry work, because that is a data
    # problem the planner should see rather than one this screen hides.
    idle = [
        g for g in groups
        if g["load"] is not None and g["load"] < idle_pct and not g["retired"]
    ]

    return {
        "start": start,
        "end": end,
        "groups": groups,
        "overloaded": overloaded,
        "idle": idle,
        "standing": standing,
        "shared_ops": _rows(db, SHARED_OP_SQL),
        "retired": [g["name"] for g in groups if g["retired"]],
        "over_pct": over_pct,
        "idle_pct": idle_pct,
    }


# ======================================================================
# Calendar reasoning - what hours is a group NOT using?
# ======================================================================


def _secs(hms):
    h, m, sec = (int(x) for x in str(hms).split(":"))
    return h * 3600 + m * 60 + sec


def _cal_shifts(db, name):
    """
    A group's own working windows, deduplicated, as toc_pack wants them.

    Deduplication is not tidiness. Calendar buckets accumulate when a
    database is reloaded without clearing them, and a calendar holding the
    same 06-14 window twice reads as sixteen hours of morning shift. The
    screen then believes the group already works round the clock and
    quietly stops offering it the shift it actually needs - wrong advice,
    with nothing on screen to suggest anything is amiss.
    """
    seen, out = set(), []
    for r in _rows(db, CAL_SQL, [name]):
        flags = [r["sunday"], r["monday"], r["tuesday"], r["wednesday"],
                 r["thursday"], r["friday"], r["saturday"]]
        weekdays = frozenset(
            6 if i == 0 else i - 1 for i, on in enumerate(flags) if on
        )
        key = (r["s"] or "00:00:00", r["e"] or "23:59:59", weekdays)
        if key in seen:
            continue
        seen.add(key)
        out.append((key[0], key[1], set(weekdays)))
    return out


def _cal_headroom(shifts):
    """
    How much more a group could work, read from its OWN calendar.

    Returns (hours_per_working_day, working_days_per_week).

    Windows are MERGED per weekday rather than summed, so neither a
    duplicated bucket nor two genuinely overlapping shifts can inflate the
    figure past the 24 hours a day actually holds. A group already running
    around the clock has no headroom and must not be offered a shift it
    already works - that is the difference between advice and noise.
    """
    if not shifts:
        # No buckets at all means continuously available: no headroom.
        return 24.0, 7

    per_day = {}
    for s, e, wd in shifts:
        a, b = _secs(s), _secs(e)
        if b <= a:
            continue
        for d in (wd or set(range(7))):
            per_day.setdefault(d, []).append((a, b))

    if not per_day:
        return 24.0, 7

    totals = []
    for spans in per_day.values():
        spans.sort()
        merged = 0
        cur_a, cur_b = spans[0]
        for a, b in spans[1:]:
            if a <= cur_b:
                cur_b = max(cur_b, b)
            else:
                merged += cur_b - cur_a
                cur_a, cur_b = a, b
        merged += cur_b - cur_a
        totals.append(merged / 3600.0)

    # The busiest day is what a shift pattern is judged on: a plant working
    # 16h Mon-Fri and 8h on Saturday is a two-shift plant.
    return round(max(totals), 2), len(per_day)


# WHY THERE IS NO PREDICTED "SAVES YOU N DAYS" HERE
# --------------------------------------------------
# There was, briefly, and it was wrong by a factor of five.
#
# The idea was sound enough: re-pack a group's own jobs against a roomier
# calendar through toc_pack and report how much earlier the last one lands.
# Against a change whose real value had been MEASURED at 14 days, it
# returned about 70 for each of six groups.
#
# Two reasons, and the second is not fixable here:
#
#   1. The simulated calendar spans the demand horizon, while the plan
#      itself runs months past it. Packing into a shorter window and
#      comparing against the longer one manufactures a gain.
#   2. toc_pack sees ONE resource. It cannot see the parts list, or the
#      other six constrained groups a job must also pass through. A job
#      pulled earlier here simply waits somewhere else, and the simulation
#      has no way to know that.
#
# The first could be patched. The second cannot, and a number that is
# confidently five times too large is worse than no number: a planner would
# authorise a third shift expecting ten weeks and get two.
#
# So the screen states what it can prove - the capacity arithmetic, which is
# exact - and offers to MEASURE the rest by actually running it. If a
# trustworthy estimator is wanted later, the bar is that it reproduces a
# known measured result before it is allowed on screen.


# ======================================================================
# Generators - each a pure function over facts, none knows a group name
# ======================================================================


def _suggest_idle_twin(db, facts):
    """
    An overloaded group beside one doing the same kind of work but idle.

    Two signals, and the difference between them matters:

      shared    the data already routes some operation to both, so they ARE
                interchangeable for that work. A fact.
      similar   their descriptions overlap. A HYPOTHESIS, and rendered as a
                question for the plant to answer, never as an instruction.

    On a dataset whose descriptions are opaque codes the second signal
    simply finds nothing, which is correct.
    """
    out = []
    idle_by_name = {g["name"]: g for g in facts["idle"]}
    shared = {}
    for r in facts["shared_ops"]:
        shared.setdefault(r["a"], {})[r["b"]] = int(r["ops"])
        shared.setdefault(r["b"], {})[r["a"]] = int(r["ops"])

    for g in facts["overloaded"]:
        best = None
        for peer_name, peer in idle_by_name.items():
            if peer_name == g["name"]:
                continue
            n_shared = shared.get(g["name"], {}).get(peer_name, 0)
            same_section = g["section"] and peer["section"] == g["section"]
            a, b = _tokens(g["description"]), _tokens(peer["description"])
            overlap = len(a & b) / len(a | b) if (a and b) else 0.0
            if n_shared:
                score, why = 2.0 + n_shared / 1000.0, "shared"
            elif same_section and overlap >= 0.5:
                score, why = overlap, "similar"
            else:
                continue
            spare = max(0.0, peer["avail"] - peer["needs"])
            if not spare:
                continue
            if best is None or score > best[0]:
                best = (score, peer, why, spare, overlap)

        if not best:
            continue
        _score, peer, why, spare, overlap = best
        moved = min(spare, g["needs"] - g["avail"])
        after = 100.0 * (g["needs"] - moved) / g["avail"] if g["avail"] else None
        out.append({
            "key": f"twin:{g['name']}:{peer['name']}",
            "kind": "Use idle capacity",
            "certainty": "fact" if why == "shared" else "question",
            "title": (
                f"Move work from {g['name']} to {peer['name']}"
                if why == "shared"
                else f"Could {peer['name']} take work from {g['name']}?"
            ),
            "detail": (
                f"{g['name']} ({g['description'] or 'no description'}) is at "
                f"{g['load']:.0f}% of capacity. {peer['name']} "
                f"({peer['description'] or 'no description'}) has "
                f"{peer['machines']} machine(s) at {peer['load']:.0f}% with "
                f"{spare:,.0f} spare hours."
                + (
                    " The routings already send some operations to both, so "
                    "the work can go either way."
                    if why == "shared"
                    else " Their descriptions match closely, but nothing in "
                    "the data says the work is interchangeable - that is a "
                    "question for the plant, and the answer is worth having: "
                    "it would cost nothing."
                )
            ),
            "group": g["name"],
            "load_before": g["load"],
            "load_after": round(after) if after is not None else None,
            "shipments": g["late_shipments"],
            "est_days": None,
            "scenario": "relief" if why == "shared" else None,
            "sort": (2 if why == "shared" else 1, g["late_shipments"]),
        })
    return out


def _suggest_shift(db, facts):
    """
    More hours on a group that is short of them - read from its own
    calendar, so a plant already on three shifts is offered its weekend and
    one already at 24/7 is offered nothing.
    """
    out = []
    for g in facts["overloaded"]:
        hours, days = _cal_headroom(_cal_shifts(db, g["name"]))
        options = []
        if hours < 23.5:
            options.append((
                "a further shift",
                min(24.0, hours + 8.0) / hours if hours else 1.0,
            ))
        if days < 7:
            options.append(("the rest of the week", 7.0 / days))
        if not options:
            continue
        label, factor = max(options, key=lambda o: o[1])
        after = g["load"] / factor
        out.append({
            "key": f"shift:{g['name']}",
            "kind": "More hours",
            "certainty": "arithmetic",
            "title": f"Work {label} on {g['name']}",
            "detail": (
                f"{g['name']} currently works {hours:.0f} h on each of "
                f"{days} day(s) a week and needs {g['needs']:,.0f} h against "
                f"{g['avail']:,.0f} available. Adding {label} takes it to "
                f"{after:.0f}%."
                + (
                    " That still leaves it over capacity, so it relieves the "
                    "delay without removing it."
                    if after > facts["over_pct"]
                    else " That clears the overload on this group."
                )
            ),
            "group": g["name"],
            "load_before": g["load"],
            "load_after": round(after),
            "shipments": g["late_shipments"],
            "est_days": None,
            "scenario": "3shift",
            "sort": (3, g["late_shipments"]),
        })
    return out


def _suggest_machines(db, facts):
    """How many more machines would clear it - the capital question."""
    out = []
    for g in facts["overloaded"]:
        hours, days = _cal_headroom(_cal_shifts(db, g["name"]))
        per_machine = g["avail"] / max(1, g["machines"])
        # Judge against the group working flat out, so this is what remains
        # AFTER every hour has been bought.
        headroom = (24.0 / hours if hours else 1.0) * (7.0 / days if days else 1.0)
        best_case = per_machine * headroom
        if not best_case:
            continue
        need = g["needs"] / best_case
        extra = int(-(-need // 1)) - g["machines"]
        if extra <= 0:
            continue
        out.append({
            "key": f"machines:{g['name']}",
            "kind": "More machines",
            "certainty": "arithmetic",
            "title": f"{g['name']} needs {extra} more machine(s)",
            "detail": (
                f"Even running every available hour, {g['machines']} "
                f"machine(s) cannot cover {g['needs']:,.0f} h in this window. "
                f"Clearing it needs about {extra} more."
            ),
            "group": g["name"],
            "load_before": g["load"],
            "load_after": 100,
            "shipments": g["late_shipments"],
            "est_days": None,
            "scenario": None,
            "sort": (0, g["late_shipments"]),
        })
    return out


def _suggest_transfer(db, facts):
    """
    Move parts between operations faster. A global lever, so it is offered
    once, and only when the plan actually carries waiting time worth
    attacking.
    """
    row = _rows(db, """
        select count(*) as steps,
               sum(extract(epoch from posttime)/3600.0) as hours,
               avg(extract(epoch from posttime)/3600.0) as avg_hours
        from operation where posttime > interval '0'
    """)
    if not row or not row[0]["steps"]:
        return []
    r = row[0]
    work = sum(g["needs"] for g in facts["groups"])
    if not r["hours"] or r["hours"] < work * 0.25:
        return []
    return [{
        "key": "transfer",
        "kind": "Move parts sooner",
        "certainty": "arithmetic",
        "title": f"Halve the {r['avg_hours']:.0f} h wait between operations",
        "detail": (
            f"{int(r['steps']):,} routing steps each wait about "
            f"{r['avg_hours']:.0f} h before the next one starts - "
            f"{r['hours']:,.0f} h in total against {work:,.0f} h of actual "
            "machine work. Most of a job's life is spent waiting to move, "
            "not being worked on. Halving it costs nothing but scheduling "
            "discipline, though it moves work to the constraint sooner "
            "rather than increasing what the constraint can do."
        ),
        "group": None,
        "load_before": None,
        "load_after": None,
        "shipments": facts["standing"]["late"],
        "est_days": None,
        "scenario": "transfer",
        "sort": (1, 0),
    }]


def _suggest_rephase(db, facts):
    """
    Shipments promised on the same day that together need more than the
    plant can do. The one lever that needs no capacity at all - it is a
    conversation with the customer, not with the shop floor.
    """
    rows = _rows(db, """
        select date(due) as day,
               count(distinct coalesce(nullif(category,''), name)) as shipments,
               count(*) as orders
        from demand group by 1 having count(*) > 0 order by 3 desc limit 1
    """)
    if not rows or int(rows[0]["shipments"]) < 2:
        return []
    r = rows[0]
    total = _rows(db, "select count(*) as n from demand")[0]["n"] or 1
    share = 100.0 * int(r["orders"]) / total
    if share < 15:
        return []
    return [{
        "key": f"rephase:{r['day']}",
        "kind": "Re-phase promises",
        "certainty": "arithmetic",
        "title": (
            f"{int(r['shipments'])} shipments are promised for "
            f"{r['day']:%d %b}"
        ),
        "detail": (
            f"They carry {int(r['orders']):,} of the {int(total):,} orders in "
            f"the plan - {share:.0f}% of the work landing on one date. Moving "
            "the least urgent of them is the only lever here that needs no "
            "extra capacity."
        ),
        "group": None,
        "load_before": None,
        "load_after": None,
        "shipments": int(r["shipments"]),
        "est_days": None,
        "scenario": None,
        "sort": (1, int(r["shipments"])),
    }]


def _suggest_start_date(db, facts):
    """
    ONE card: pick a late shipment, pick a start date, measure what happens.

    It used to be a card per shipment, which on this plan meant 34 of them -
    they buried the plant-wide advice under a page of scrolling and repeated
    the same sentence and the same date 34 times, because every late
    shipment already starts on the plan's own first day. The per-shipment
    list also already exists, in full, on the Delivery sequences screen.

    So the shipments are a dropdown on one card. Nothing here predicts the
    answer - the card carries arithmetic only (how late, when it currently
    starts) until a what-if has actually run, which is what prove() does.
    See the note above _suggest_idle_twin for why there is no estimate.

    A plan with nothing late gets no card at all.
    """
    now = _plan_now(db)

    # Measurements already taken against THIS plan. _suggestions() attaches
    # those by the card's own key, which is no use now that one card covers
    # every shipment - so they are looked up per shipment here, against the
    # same fingerprint and through the same formatter.
    measured = _json_param(db, P_MEASURED, {})
    if not isinstance(measured, dict):
        measured = {}
    fp = _fingerprint(db, facts)

    ships = []
    at_floor = 0
    for r in _rows(db, LATE_SHIPMENT_SQL):
        # Nothing to offer when the plan never scheduled it - a start date
        # cannot fix an order the engine could not place at all.
        if r["finish"] is None or r["due"] is None:
            continue
        delay = (r["finish"] - r["due"]).total_seconds() / 86400.0
        if delay <= 0:
            continue

        starts = r["first_start"]
        # Is the plan already starting this on its own first day? Counted,
        # never assumed - it decides what the card can honestly offer.
        if starts and starts <= now + timedelta(days=1):
            at_floor += 1

        m = measured.get("start:%s" % r["tag"])
        ships.append({
            "tag": r["tag"],
            "orders": int(r["orders"] or 0),
            "unplanned": int(r["unplanned"] or 0),
            "delay": round(delay, 1),
            "due": r["due"],
            "finish": r["finish"],
            "start": starts,
            # A measurement belongs to the plan it was taken against.
            "measured_text": (
                _measured_sentence(m.get("detail"))
                if m and m.get("fingerprint") == fp else ""
            ),
        })

    if not ships:
        return []

    # LATE_SHIPMENT_SQL already orders worst first; keep that for the
    # dropdown and take the worst as the card's headline.
    worst = ships[0]
    n = len(ships)

    # Plain text only - the template escapes this, and the tags inside it
    # come from the database.
    detail = (
        f"{n} shipment{'s' if n != 1 else ''} will miss "
        f"{'their' if n != 1 else 'its'} date, the worst by "
        f"{worst['delay']:.1f} days. "
    )
    if at_floor == n:
        detail += (
            "Every one of them already starts on the plan's own first day, "
            "so none can start earlier and this lateness is capacity rather "
            "than a late start. Starting one later is still worth testing: "
            "it frees capacity for the others, and the measurement says what "
            "that costs. "
        )
    elif at_floor:
        detail += (
            f"{at_floor} of them already start on the plan's own first day "
            "and cannot start earlier. "
        )
    detail += (
        "Choose a shipment and a start date, then press Measure: the plan is "
        "copied, that shipment's start is pinned to your date, and the copy "
        "is replanned. The ship date reported back is the one the engine "
        "actually produced - this card never estimates. Per-shipment figures "
        "are on the Delivery sequences screen."
    )

    return [{
        "key": "startdate",
        "kind": "Start on a different date",
        "certainty": "arithmetic",
        "title": (
            f"{n} shipment{'s' if n != 1 else ''} late - "
            "test a different start date"
        ),
        "detail": detail,
        "group": None,
        "load_before": None,
        "load_after": None,
        "shipments": n,
        # Never predicted. Measured or nothing.
        "est_days": None,
        "scenario": "startdate",
        # Its own tier, above the plant-wide levers. One card now, so the
        # magnitude only has to beat nothing.
        "sort": (4, worst["delay"]),
        # -- the card's own controls -----------------------------------
        "ships": ships,
        # You cannot start in the past. The date box is bounded by the
        # plan's own current date, not by the browser's clock.
        "ship_min_start": now,
    }]


GENERATORS = (
    _suggest_idle_twin,
    _suggest_shift,
    _suggest_machines,
    _suggest_transfer,
    _suggest_rephase,
    _suggest_start_date,
)


def _measured_sentence(d):
    """
    A stored start-date measurement, in one sentence of plain English.

    Plain text, not markup: the template escapes it, and the shipment tag
    inside it is data.
    """
    if not isinstance(d, dict) or not d.get("ships"):
        return ""

    def day(iso):
        try:
            return f"{datetime.fromisoformat(iso):%d %b %Y}"
        except (TypeError, ValueError):
            return str(iso or "")

    late = d.get("late_days")
    if late is None:
        verdict = ""
    elif late > 0.05:
        verdict = f", {late:.1f} days late"
    elif late < -0.05:
        verdict = f", {abs(late):.1f} days early"
    else:
        verdict = ", on the due date"

    gained = d.get("gained_days")
    if gained is None:
        moved = ""
    elif gained > 0.05:
        moved = f" That is {gained:.1f} days sooner than the current plan."
    elif gained < -0.05:
        moved = f" That is {abs(gained):.1f} days later than the current plan."
    else:
        moved = (
            " The ship date did not move at all - on this plan the start date "
            "is not what is holding it."
        )

    k = d.get("knock_on") or {}
    later, earlier, worst = k.get("later", 0), k.get("earlier", 0), k.get("worst")
    if later:
        cost = f" Cost elsewhere: {later} other shipment{'' if later == 1 else 's'} slipped"
        if worst:
            cost += f", worst {worst[0]} by {worst[1]} days"
        cost += "."
        if earlier:
            cost += f" {earlier} improved."
    elif earlier:
        cost = f" Nothing else slipped; {earlier} other shipments improved."
    else:
        cost = " Nothing else in the plan moved."

    return (
        f"Measured on a copy ({d.get('slot')}), {d.get('pinned')} jobs pinned "
        f"to {day(d.get('date'))}: ships {day(d.get('ships'))}{verdict}."
        f"{moved}{cost}"
    )


def _suggestions(db, facts):
    out = []
    for gen in GENERATORS:
        try:
            out.extend(gen(db, facts))
        except Exception as exc:  # noqa: BLE001
            # One generator failing must not blank the page. The planner
            # still gets every other suggestion, and the failure is visible
            # rather than silent.
            out.append({
                "key": f"error:{gen.__name__}",
                "kind": "Not available",
                "certainty": "error",
                "title": f"'{gen.__name__}' could not run",
                "detail": f"{type(exc).__name__}: {exc}",
                "group": None, "load_before": None, "load_after": None,
                "shipments": 0, "est_days": None, "scenario": None,
                "sort": (-1, 0),
            })

    # Set-aside records carry the situation at the time, not just the key.
    # A judgement made when a resource was at 110% is not a judgement about
    # the same resource at 400%, and a decision-support screen that keeps
    # hiding a problem because someone waved it away months ago is worse
    # than one that never had the button.
    dismissed = _json_param(db, P_DISMISSED, {})
    if isinstance(dismissed, list):          # the original flat-list format
        dismissed = {k: {} for k in dismissed}
    measured = _json_param(db, P_MEASURED, {})
    fp = _fingerprint(db, facts)

    for s in out:
        m = measured.get(s["key"])
        # A measurement belongs to the plan it was taken against. Once the
        # plan moves, it is history, not advice.
        s["measured"] = m if (m and m.get("fingerprint") == fp) else None
        # Re-state it in words, so the answer survives a page reload rather
        # than living only in the javascript that first received it.
        s["measured_text"] = (
            _measured_sentence(s["measured"].get("detail"))
            if s["measured"] else ""
        )

        rec = dismissed.get(s["key"])
        s["set_aside"] = rec
        s["dismissed"] = rec is not None
        s["resurfaced"] = False
        if rec and s["load_before"] and rec.get("load"):
            # Materially worse than when it was judged: put it back, and say
            # why, so nobody thinks the screen forgot their decision.
            if s["load_before"] > rec["load"] * RESURFACE_FACTOR:
                s["dismissed"] = False
                s["resurfaced"] = True

    out.sort(key=lambda s: (-s["sort"][0], -s["sort"][1]))
    return out


# ======================================================================
# Views
# ======================================================================


@login_required(login_url="/data/login/")
def improve(request):
    db = _db(request)
    facts = _facts(db)
    all_suggestions = _suggestions(db, facts)
    running = _running_replan(db)
    return render(request, "sieger_ui/improve.html", {
        "facts": facts,
        "standing": facts["standing"],
        "constraints": facts["overloaded"][:10],
        "suggestions": [s for s in all_suggestions if not s["dismissed"]],
        "set_aside": [s for s in all_suggestions if s["dismissed"]],
        "running": running,
        "fingerprint": _fingerprint(db, facts),
        "has_plan": bool(facts["groups"]),
    })


# ======================================================================
# Measuring a start date, for real.
#
# The screen may not predict, so it runs the change and reads the answer -
# scenario_copy, then the pin, then a replan, on a COPY. Three steps, each
# taking minutes, so they cannot happen inside one request: prove() starts
# the first and status() walks the rest as the planner polls.
#
# The previous version of this endpoint returned 501 and listed what it
# needed. Each of those is answered here:
#
#   * the slot is chosen at runtime from improve.slots, never fixed, and
#     the database being viewed is never a candidate;
#   * copy -> change -> replan are chained, and the copy is VERIFIED (the
#     order count must match) before the change is applied;
#   * a result smaller than the control run's own variation is reported as
#     "within noise" rather than as a number.
#
# The shift traps in scenario_run.apply_3shift_drums are not answered here
# because this path only pins dates; a shift suggestion still cannot be
# measured from this screen.
# ======================================================================


def _measuring(db):
    st = _json_param(db, P_MEASURING, {})
    return st if isinstance(st, dict) else {}


def _set_measuring(db, st):
    _set_param(db, P_MEASURING, json.dumps(st), "Measurement in flight")


def _slots(db, current):
    """
    Scenario aliases this screen is allowed to overwrite.

    Never the database being viewed, and never one the admin has not named.
    Guessing would eventually pick the live plan and replace it with a
    what-if, which is unrecoverable.
    """
    raw = _param(db, P_SLOTS, "")
    named = [s.strip() for s in raw.split(",") if s.strip()]
    known = set(get_databases())
    return [s for s in named if s in known and s != current]


def _free_slot(db, current):
    for slot in _slots(db, current):
        if not _running_replan(slot):
            return slot
    return None


def _task_state(db, task_id):
    t = Task.objects.using(db).filter(id=task_id).first()
    if not t:
        return "missing", ""
    if t.status in ("Done",):
        return "done", t.status
    if t.status in ("Failed", "Canceled"):
        return "failed", t.message or t.status
    return "running", t.status or ""


# Pin the first step of every job feeding one shipment. 'confirmed' is the
# frozen status - the engine hard-sets those dates and a replan rearranges
# everything else around them. Same mechanism the drag-and-drop board uses.
#
# The casts are load-bearing: without them Postgres infers the second
# parameter from '+ (enddate - startdate)' and tries to read a date as an
# interval.
PIN_STEP_SQL = """
with mine as (
    select distinct op.owner_id as mo
    from operationplan op
    cross join lateral jsonb_object_keys(
        coalesce(op.plan->'pegging', '{}'::jsonb)) as pegged(k)
    join demand d on d.name = pegged.k
    where op.type = 'WO' and op.owner_id is not null
      and coalesce(nullif(d.category, ''), d.name) = %s
),
firsts as (
    select op.reference,
           row_number() over (partition by op.owner_id order by op.startdate) as rn
    from operationplan op
    where op.type = 'WO' and op.owner_id in (select mo from mine)
)
update operationplan op
set startdate = %s::timestamptz,
    enddate = %s::timestamptz + (op.enddate - op.startdate),
    status = 'confirmed'
from firsts f
where f.reference = op.reference and f.rn = 1
"""

# Confirming the step alone is NOT enough, and getting this wrong makes the
# whole measurement a lie: runplan NULLs the owner of any child whose parent
# is still 'proposed' and then deletes it, so the pin silently vanishes and
# the "measured" ship date is the unchanged plan compared against itself.
# The parent run is therefore confirmed too, with its envelope WIDENED to
# contain the pin rather than moved - exactly what moves.move_apply does.
# Siblings stay proposed so the engine can still re-place them around it.
PIN_PARENT_SQL = """
update operationplan mo
set status = 'confirmed',
    startdate = least(mo.startdate, c.startdate),
    enddate = greatest(mo.enddate, c.enddate)
from (
    select owner_id, min(startdate) as startdate, max(enddate) as enddate
    from operationplan
    where status = 'confirmed' and type = 'WO' and owner_id is not null
    group by owner_id
) c
where mo.reference = c.owner_id and mo.type = 'MO'
"""

SHIP_SQL = """
select max(deliverydate) as finish, min(due) as due, count(*) as orders
from demand
where coalesce(nullif(category, ''), name) = %s
"""


def _shipment_dates(db, tag):
    r = _rows(db, SHIP_SQL, [tag])
    return r[0] if r else None


def _knock_on(base_db, other_db, tag):
    """
    What the pin cost everyone else. The same cross-database comparison the
    /compare/ screen makes, narrowed to shipments other than this one.
    """
    base = {
        d["tag"]: d["finish"]
        for d in _rows(base_db, """
            select coalesce(nullif(category,''), name) as tag,
                   max(deliverydate) as finish
            from demand group by 1
        """)
    }
    other = {
        d["tag"]: d["finish"]
        for d in _rows(other_db, """
            select coalesce(nullif(category,''), name) as tag,
                   max(deliverydate) as finish
            from demand group by 1
        """)
    }
    later = earlier = 0
    worst = None
    for t, before in base.items():
        after = other.get(t)
        if t == tag or not before or not after:
            continue
        shift = (after - before).total_seconds() / 86400.0
        if abs(shift) < 0.04:          # under an hour is not a move
            continue
        if shift > 0:
            later += 1
            if worst is None or shift > worst[1]:
                worst = (t, round(shift, 1))
        else:
            earlier += 1
    return {"later": later, "earlier": earlier, "worst": worst}


@require_POST
@login_required(login_url="/data/login/")
def prove(request):
    db = _db(request)
    key = (request.POST.get("key") or "").strip()
    raw_date = (request.POST.get("date") or "").strip()

    if not key.startswith("start:"):
        return JsonResponse(
            {"error": "Only a start-date card can be measured from here."},
            status=400,
        )
    tag = key[len("start:"):]

    try:
        when = datetime.strptime(raw_date, "%Y-%m-%d")
    except ValueError:
        return JsonResponse({"error": "Give a date as YYYY-MM-DD."}, status=400)

    now = _plan_now(db)
    if when.date() < now.date():
        return JsonResponse(
            {"error": f"The plan starts on {now:%d %b %Y}. You cannot start "
                      "manufacturing before that."},
            status=400,
        )

    live = _measuring(db)
    if live and live.get("phase") not in ("done", "failed", None):
        return JsonResponse(
            {"error": f"A measurement of {live.get('tag')} is already running. "
                      "Only one can run at a time - two solves at once "
                      "deadlock."},
            status=409,
        )

    if not _slots(db, db):
        return JsonResponse(
            {"error": "Measuring is switched off: no scenario slot is set "
                      "aside for it. A measurement REPLACES a scenario "
                      "database, and scenario1, 3 and 4 currently hold the "
                      "12h, 8h and 6h example plans - which is "
                      "why improve.slots was emptied. To switch this back "
                      "on, create a spare scenario and name it in "
                      "improve.slots under Admin - Parameters. The plan you "
                      "are looking at is never a candidate."},
            status=409,
        )

    slot = _free_slot(db, db)
    if not slot:
        return JsonResponse(
            {"error": "Every scenario slot is busy planning. Wait for those "
                      "to finish - concurrent solves deadlock."},
            status=409,
        )

    # Nothing may be planning anywhere, in any database: the solver is one
    # process and two runs deadlock at 72%.
    for alias in get_databases():
        busy = _running_replan(alias)
        if busy:
            return JsonResponse(
                {"error": f"A plan is still running in '{alias}'. Wait for it "
                          "to finish."},
                status=409,
            )

    # The task runner splits this string on whitespace, so the description
    # must not contain any - a space turns the rest of it into stray
    # positional arguments and scenario_copy rejects the lot.
    task = Task(
        name="scenario_copy",
        submitted=timezone.now(),
        status="Waiting",
        user=request.user,
        arguments=f"{db} {slot} --force --description=start-date-what-if:{tag}",
    )
    task.save(using=db)
    launchWorker(database=db)

    _set_measuring(db, {
        "key": key, "tag": tag, "date": raw_date, "slot": slot,
        "phase": "copying", "task": task.id, "source": db,
        "orders": (_shipment_dates(db, tag) or {}).get("orders"),
    })
    return JsonResponse({"phase": "copying", "slot": slot, "done": False})


@login_required(login_url="/data/login/")
def status(request):
    """
    Polls the measurement forward. Each call advances at most one phase, so
    the work happens on the planner's own polling and never blocks a request
    for longer than one step.
    """
    db = _db(request)
    st = _measuring(db)

    if not st or st.get("phase") in ("done", "failed"):
        # No measurement in flight - answer the way this endpoint always
        # did, so the drag-and-drop banner keeps working.
        if st.get("phase") in ("done", "failed"):
            return JsonResponse({
                "done": True, "phase": st["phase"], "status": "",
                "result": st.get("result"), "error": st.get("error"),
                "key": st.get("key"),
            })
        task = _running_replan(db)
        if task:
            return JsonResponse({"done": False, "status": task.status or ""})
        return JsonResponse({"done": True, "status": "Done"})

    phase = st.get("phase")
    slot = st.get("slot")

    def fail(msg):
        st["phase"] = "failed"
        st["error"] = msg
        _set_measuring(db, st)
        return JsonResponse({"done": True, "phase": "failed", "error": msg,
                             "key": st.get("key")})

    # ---- 1. the copy ---------------------------------------------------
    if phase == "copying":
        state, note = _task_state(db, st["task"])
        if state == "running":
            return JsonResponse({"done": False, "phase": "copying",
                                 "status": note, "key": st.get("key")})
        if state != "done":
            return fail(f"The copy into {slot} did not finish: {note}")

        # VERIFY it before touching it. A copy that silently produced an
        # empty database would make the pin a no-op and the measurement a
        # comparison of the plan against itself.
        src = _shipment_dates(db, st["tag"])
        dst = _shipment_dates(slot, st["tag"])
        if not dst or not dst.get("orders") or (
            src and dst["orders"] != src["orders"]
        ):
            return fail(
                f"The copy into {slot} does not match this plan "
                f"({(dst or {}).get('orders')} orders against "
                f"{(src or {}).get('orders')}). Nothing was changed."
            )

        st["before"] = {
            "finish": src["finish"].isoformat() if src and src["finish"] else None,
            "due": src["due"].isoformat() if src and src["due"] else None,
        }

        # ---- 2. the change, in the copy only --------------------------
        try:
            with connections[slot].cursor() as c:
                c.execute(PIN_STEP_SQL, [st["tag"], st["date"], st["date"]])
                pinned = c.rowcount
                c.execute(PIN_PARENT_SQL)
                parents = c.rowcount
        except Exception as exc:  # noqa: BLE001
            return fail(f"Pinning the start date failed: {exc}")
        if not pinned:
            return fail(
                f"No job could be pinned for {st['tag']} - the plan holds no "
                "routing steps pegged to it."
            )
        st["pinned"] = pinned
        st["parents"] = parents

        # ---- 3. replan the copy ---------------------------------------
        task = Task(
            name="runplan", submitted=timezone.now(), status="Waiting",
            user=request.user, arguments=RUNPLAN_ARGS,
        )
        task.save(using=slot)
        launchWorker(database=slot)
        st["task"] = task.id
        st["phase"] = "planning"
        _set_measuring(db, st)
        return JsonResponse({"done": False, "phase": "planning",
                             "pinned": pinned, "key": st.get("key")})

    # ---- 4. read the answer -------------------------------------------
    if phase == "planning":
        state, note = _task_state(slot, st["task"])
        if state == "running":
            return JsonResponse({"done": False, "phase": "planning",
                                 "status": note, "key": st.get("key")})
        if state != "done":
            return fail(f"The replan of {slot} did not finish: {note}")

        after = _shipment_dates(slot, st["tag"])
        if not after or not after["finish"]:
            return fail("The replan left this shipment with no delivery date.")

        # Did the pin SURVIVE? A replan that deleted it leaves the copy
        # identical to the source, and the honest report of that is "the
        # measurement did not hold", never "no change" - the two look the
        # same on screen and only one of them is true.
        held = _rows(slot, """
            select count(*) as n, min(startdate) as first_start
            from operationplan where type = 'WO' and status = 'confirmed'
        """)
        if not held or not held[0]["n"]:
            return fail(
                "The pin did not survive the replan - the engine removed the "
                "confirmed steps, so the copy is identical to the live plan "
                "and nothing was actually measured."
            )

        before_finish = st.get("before", {}).get("finish")
        before_dt = datetime.fromisoformat(before_finish) if before_finish else None
        due = after["due"]
        gained = (
            (before_dt - after["finish"]).total_seconds() / 86400.0
            if before_dt else None
        )
        result = {
            "tag": st["tag"],
            "date": st["date"],
            "slot": slot,
            "pinned": st.get("pinned"),
            "ships": after["finish"].isoformat(),
            "due": due.isoformat() if due else None,
            "late_days": round((after["finish"] - due).total_seconds() / 86400.0, 1)
            if due else None,
            "gained_days": round(gained, 1) if gained is not None else None,
            "was": before_finish,
            "knock_on": _knock_on(db, slot, st["tag"]),
        }
        st["phase"] = "done"
        st["result"] = result
        _set_measuring(db, st)

        # Keep it with the other measurements, stamped with the plan it was
        # taken against so it disappears when that plan changes.
        measured = _json_param(db, P_MEASURED, {})
        if not isinstance(measured, dict):
            measured = {}
        measured[st["key"]] = {
            "fingerprint": _fingerprint(db, _facts(db)),
            "days": result["gained_days"],
            "detail": result,
        }
        _set_param(db, P_MEASURED, json.dumps(measured), "Measured results")

        return JsonResponse({"done": True, "phase": "done", "result": result,
                             "key": st.get("key")})

    return fail(f"Unknown measurement phase '{phase}'.")


@require_POST
@login_required(login_url="/data/login/")
def dismiss(request):
    """
    Set a suggestion aside, or bring one back.

    Records the situation at the moment of the decision - the load and the
    date - for two reasons. It lets the screen resurface the card if things
    get materially worse, and it lets the set-aside list say WHEN and at
    WHAT the judgement was made, so a stale decision is visible as stale.

    The first version stored a bare list of keys. That made the button a
    trapdoor: once a card was hidden there was nothing left on screen to
    press, and the only way back was editing a parameter by hand in Admin.
    """
    db = _db(request)
    key = request.POST.get("key", "")
    if not key:
        return JsonResponse({"error": "no suggestion named"}, status=400)

    current = _json_param(db, P_DISMISSED, {})
    if isinstance(current, list):
        current = {k: {} for k in current}

    if key in current:
        current.pop(key)
        action = "restored"
    else:
        facts = _facts(db)
        match = next(
            (x for x in _suggestions(db, facts) if x["key"] == key), None
        )
        current[key] = {
            "at": _plan_now(db).strftime("%Y-%m-%d"),
            "load": match["load_before"] if match else None,
            "title": match["title"] if match else key,
        }
        action = "set aside"

    _set_param(db, P_DISMISSED, json.dumps(current, default=str),
               "Suggestions the planner has set aside, with the situation "
               "at the time so the screen can raise them again if it worsens")
    return JsonResponse({"ok": True, "action": action})
