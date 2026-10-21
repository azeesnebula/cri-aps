"""
The shipment map - the heart screen, rebuilt as a progressive mind map.

    GET /build-graph/                      the map
    GET /build-graph/map/ships/            all shipments, placed on the time axis
    GET /build-graph/map/<tag>/sections/   one shipment's operation groups
    GET /build-graph/map/<tag>/<sec>/runs/ the runs inside one group (paged)

WHY THIS REPLACED THE ALL-AT-ONCE GRAPH
---------------------------------------
The first version drew every production run of a shipment at once: up to
2,148 cards and 2,400 wires, which was both unreadable and slow. The data
is simply too wide to show whole - one shipment has 1,580 runs feeding it
DIRECTLY, so even "one level" is not a picture a person can read.

So nothing loads until it is asked for, and the first level is not runs
but OPERATION GROUPS - the ~20 sections of the plant the work passes
through (laser cutting, MIG welding, powder coat, textile assembly...).
That is both the shape a planner already thinks in and small enough to
stay instant. Each group then opens into its own runs, paged.

SECTION FLOW IS DERIVED FROM THE PLAN, NOT ASSUMED
--------------------------------------------------
Which section feeds which is read off each run's OWN step order: if a run
does a step in SMLS1 and its next step is in QUAL, then laser cutting
feeds quality. Aggregated over a shipment that yields the real process
flow (SMMG1 -> SMPC1 -> TASY ...). No routing diagram is assumed and no
order is invented; a section with no successor is one that feeds the
shipment itself.

A RUN CAN BE IN SEVERAL SECTIONS, ON PURPOSE
--------------------------------------------
A production run's steps are spread over different machines, so the same
run is counted under every section it passes through. A section node
therefore reads "the work this shipment has in this section", which is
the question being asked - not a partition of runs.
"""

from django.contrib.auth.decorators import login_required
from django.db import DEFAULT_DB_ALIAS, connections
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from .views import _fmt, _num, _plan_now, _ship_names

RUN_PAGE = 60          # runs returned per section request
LATE_EPS = 0.05        # days; below this a delay is rounding, not lateness


def _db(request):
    return getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS


def _rows(db, sql, params=None):
    with connections[db].cursor() as c:
        c.execute(sql, params or [])
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


def _iso(dt):
    return dt.isoformat() if dt else None


def _tone(delay_days, criticality=0):
    if float(criticality or 0) >= 999:
        return "crit"
    return "late" if (delay_days or 0) > LATE_EPS else "ok"


# ---------------------------------------------------------------- level 0

SHIPS_SQL = """
select coalesce(nullif(d.category, ''), d.name) as tag,
       max(i.description) as item_descr,
       count(*) as orders,
       min(due) as due,
       max(deliverydate) as finish,
       count(*) filter (where deliverydate is null) as unplanned,
       max(extract(epoch from delay)) / 86400.0 as late_days
from demand d
left join item i on i.name = coalesce(nullif(d.category, ''), d.name)
where d.status in ('open', 'quote')
group by 1
"""

# The machine that blocks each shipment most, from the engine's own
# records of where it could not place an order on time.
TOPBLOCK_SQL = """
select distinct on (tag) tag, owner, n from (
  select coalesce(nullif(d.category, ''), d.name) as tag,
         c.owner, count(distinct c.demand) as n
  from out_constraint c
  join demand d on d.name = c.demand
  where c.name = 'overload'
  group by 1, 2) t
order by tag, n desc
"""


# ---------------------------------------------------------------- level 1

# Every MO serving this shipment. ?| against the GIN index on
# (plan->'pegging'); the exists/jsonb_object_keys form scanned all 56k
# operationplans and cost ~1s a call.
_MO = """
with dm as (
  select name from demand where coalesce(nullif(category, ''), name) = %s),
mo as (
  select op.reference, op.quantity, op.item_id, op.startdate, op.enddate,
         op.delay, op.criticality, op.plan
  from operationplan op
  where op.type = 'MO'
    and op.plan->'pegging' ?| (select array_agg(name) from dm))
"""

SECTIONS_SQL = _MO + """,
steps as (
  select w.owner_id as run,
         coalesce(r.owner_id, opr.resource_id) as section,
         w.startdate, w.enddate,
         extract(epoch from w.delay)/86400.0 as delay_d
  from operationplan w
  join operationplanresource opr on opr.operationplan_id = w.reference
  join resource r on r.name = opr.resource_id
  where w.type = 'WO' and w.owner_id in (select reference from mo))
select s.section,
       coalesce(g.description, '') as descr,
       count(distinct s.run) as runs,
       count(distinct s.run) filter (where s.delay_d > %s) as late_runs,
       round(max(s.delay_d)::numeric, 1) as worst_late,
       min(s.startdate) as startdate,
       max(s.enddate) as enddate,
       (select count(*) from resource m where m.owner_id = s.section) as machines
from steps s
left join resource g on g.name = s.section
group by 1, 2
"""

# Section A feeds section B when some run's step in A is immediately
# followed by a step in B. The plan's own sequencing, aggregated.
SECFLOW_SQL = _MO + """,
steps as (
  select w.owner_id as run,
         coalesce(r.owner_id, opr.resource_id) as section,
         row_number() over (partition by w.owner_id
                            order by w.startdate, w.operation_id) as rn
  from operationplan w
  join operationplanresource opr on opr.operationplan_id = w.reference
  join resource r on r.name = opr.resource_id
  where w.type = 'WO' and w.owner_id in (select reference from mo))
select a.section as src, b.section as dst, count(*) as n
from steps a
join steps b on b.run = a.run and b.rn = a.rn + 1
where a.section <> b.section
group by 1, 2
"""


# ---------------------------------------------------------------- level 2

RUNS_SQL = _MO + """,
steps as (
  select w.owner_id as run,
         string_agg(distinct opr.resource_id, ', ') as machines,
         count(*) as nsteps
  from operationplan w
  join operationplanresource opr on opr.operationplan_id = w.reference
  join resource r on r.name = opr.resource_id
  where w.type = 'WO' and w.owner_id in (select reference from mo)
    and coalesce(r.owner_id, opr.resource_id) = %s
  group by 1)
select m.reference, m.item_id, coalesce(i.description, '') as item_descr,
       m.quantity, m.startdate, m.enddate,
       round((extract(epoch from m.delay)/86400.0)::numeric, 1) as delay_d,
       m.criticality, s.machines, s.nsteps,
       (select count(distinct coalesce(nullif(d2.category, ''), d2.name))
        from jsonb_object_keys(coalesce(m.plan->'pegging', '{}'::jsonb)) k(d)
        join demand d2 on d2.name = k.d) as ships
from mo m
join steps s on s.run = m.reference
left join item i on i.name = m.item_id
order by m.delay desc nulls last, m.enddate desc
"""


# ------------------------------------------------------------------ views


@login_required(login_url="/data/login/")
def shipmap(request):
    from .delivery import blocker_cards

    db = _db(request)
    by_machine = {}
    for r in _rows(db, BLOCK_SHIPS_SQL):
        by_machine.setdefault(r["machine"], []).append(r["tag"])

    cards = blocker_cards(db)
    for c in cards:
        c["ships"] = by_machine.get(c["machine"], [])
        c["top_ship"] = c["ships"][0] if c["ships"] else ""

    return render(request, "sieger_ui/shipmap.html", {
        "title": _("build graph"),
        "now": _plan_now(db),
        "blockers": cards,
    })


@login_required(login_url="/data/login/")
def ships_data(request):
    db = _db(request)
    block = {b["tag"]: b for b in _rows(db, TOPBLOCK_SQL)}
    names = _ship_names(db)

    out = []
    for s in _rows(db, SHIPS_SQL):
        late = round(float(s["late_days"] or 0), 1)
        b = block.get(s["tag"])
        out.append({
            "id": "S:" + s["tag"],
            "tag": s["tag"],
            "name": names.get(s["tag"]) or s["tag"],
            "kind": "ship",
            "orders": s["orders"],
            "due": _iso(s["due"]),
            "due_txt": _fmt(s["due"]),
            "end": _iso(s["finish"]),
            "end_txt": _fmt(s["finish"]),
            "unplanned": s["unplanned"],
            "delay_days": late,
            "tone": "crit" if s["unplanned"] else _tone(late),
            "blocker": b["owner"] if b else "",
            "blocker_orders": b["n"] if b else 0,
        })
    # Latest ship date first - the time axis runs downward into the past,
    # the same direction the old graph used.
    out.sort(key=lambda r: (r["end"] is None, r["end"] or ""), reverse=True)
    return JsonResponse({"ships": out})


@login_required(login_url="/data/login/")
def sections_data(request, tag):
    db = _db(request)
    secs = _rows(db, SECTIONS_SQL, [tag, LATE_EPS])
    if not secs:
        raise Http404("no sections for this shipment")

    flow = _rows(db, SECFLOW_SQL, [tag])
    names = {s["section"] for s in secs}
    edges = [(f["src"], f["dst"]) for f in flow
             if f["src"] in names and f["dst"] in names]

    # A section with no successor inside this shipment hands its work
    # straight to the delivery - those are the shipment's own children.
    has_succ = {a for a, _b in edges}
    nodes = []
    for s in secs:
        late = int(s["late_runs"] or 0)
        nodes.append({
            "id": "G:" + tag + ":" + s["section"],
            "section": s["section"],
            "kind": "group",
            "tag": tag,
            "descr": s["descr"],
            "runs": s["runs"],
            "late_runs": late,
            "worst_late": float(s["worst_late"] or 0),
            "machines": s["machines"],
            "start": _iso(s["startdate"]),
            "end": _iso(s["enddate"]),
            "start_txt": _fmt(s["startdate"]),
            "end_txt": _fmt(s["enddate"]),
            "tone": "late" if late else "ok",
            "feeds_ship": s["section"] not in has_succ,
        })
    nodes.sort(key=lambda n: (n["end"] is None, n["end"] or ""), reverse=True)

    return JsonResponse({
        "tag": tag,
        "nodes": nodes,
        "edges": [{"src": "G:" + tag + ":" + a,
                   "dst": "G:" + tag + ":" + b} for a, b in edges],
    })


@login_required(login_url="/data/login/")
def runs_data(request, tag, section):
    db = _db(request)
    try:
        page = max(0, int(request.GET.get("page", 0)))
    except ValueError:
        page = 0

    rows = _rows(db, RUNS_SQL, [tag, section])
    total = len(rows)
    window = rows[page * RUN_PAGE:(page + 1) * RUN_PAGE]

    out = []
    for r in window:
        delay = float(r["delay_d"] or 0)
        ships = int(r["ships"] or 1)
        out.append({
            "id": "R:" + tag + ":" + section + ":" + r["reference"],
            "ref": r["reference"],
            "kind": "run",
            "tag": tag,
            "section": section,
            "item": r["item_id"] or "",
            "descr": r["item_descr"],
            "qty": _num(r["quantity"]),
            "start": _iso(r["startdate"]),
            "end": _iso(r["enddate"]),
            "start_txt": _fmt(r["startdate"]),
            "end_txt": _fmt(r["enddate"]),
            "delay_days": delay,
            "machines": r["machines"] or "",
            "steps": r["nsteps"],
            "ships": ships,
            "shared": ships > 1,
            "tone": _tone(delay, r["criticality"]),
        })

    return JsonResponse({
        "tag": tag,
        "section": section,
        "parent": "G:" + tag + ":" + section,
        "nodes": out,
        "total": total,
        "page": page,
        "pages": max(1, -(-total // RUN_PAGE)),
        "late_total": sum(1 for r in rows if float(r["delay_d"] or 0) > LATE_EPS),
    })

# --------------------------------------------------- search, plant-wide
#
# The map only holds what a planner has opened, so searching the loaded
# cards alone would quietly miss most of the plan. These run against the
# database instead, so a part or a machine is findable from a cold page
# and the result carries the path (shipment -> section) needed to open
# its branch.

SEARCH_SEC_SQL = """
select coalesce(nullif(d.category, ''), d.name) as tag,
       coalesce(r.owner_id, opr.resource_id) as section,
       coalesce(g.description, '') as descr,
       count(distinct w.owner_id) as runs
from operationplan w
join operationplanresource opr on opr.operationplan_id = w.reference
join resource r on r.name = opr.resource_id
left join resource g on g.name = coalesce(r.owner_id, opr.resource_id)
join operationplan mo on mo.reference = w.owner_id
cross join lateral jsonb_object_keys(
    coalesce(mo.plan->'pegging', '{}'::jsonb)) k(dn)
join demand d on d.name = k.dn
where w.type = 'WO'
  and (coalesce(r.owner_id, opr.resource_id) ilike %s
       or coalesce(g.description, '') ilike %s
       or opr.resource_id ilike %s)
group by 1, 2, 3
order by 4 desc
limit 25
"""

SEARCH_RUN_SQL = """
select coalesce(nullif(d.category, ''), d.name) as tag,
       coalesce(r.owner_id, opr.resource_id) as section,
       mo.reference, mo.item_id,
       coalesce(i.description, '') as item_descr,
       round((extract(epoch from mo.delay)/86400.0)::numeric, 1) as delay_d
from operationplan mo
join operationplan w on w.owner_id = mo.reference and w.type = 'WO'
join operationplanresource opr on opr.operationplan_id = w.reference
join resource r on r.name = opr.resource_id
left join item i on i.name = mo.item_id
cross join lateral jsonb_object_keys(
    coalesce(mo.plan->'pegging', '{}'::jsonb)) k(dn)
join demand d on d.name = k.dn
where mo.type = 'MO'
  and (mo.item_id ilike %s or coalesce(i.description, '') ilike %s
       or mo.reference ilike %s)
group by 1, 2, 3, 4, 5, 6
order by 6 desc nulls last
limit 25
"""


@login_required(login_url="/data/login/")
def search_data(request):
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"q": q, "hits": []})
    db = _db(request)
    like = "%" + q + "%"
    hits = []

    for s in _rows(db, SHIPS_SQL):
        if q.lower() in s["tag"].lower():
            hits.append({
                "kind": "ship", "tag": s["tag"],
                "main": s["tag"],
                "sub": "shipment · %d orders" % s["orders"],
                "path": [s["tag"]],
            })

    for r in _rows(db, SEARCH_SEC_SQL, [like, like, like]):
        hits.append({
            "kind": "group", "tag": r["tag"], "section": r["section"],
            "main": r["section"],
            "sub": "%s · %d runs in %s" % (
                r["descr"] or "section", r["runs"], r["tag"]),
            "path": [r["tag"], r["section"]],
        })

    for r in _rows(db, SEARCH_RUN_SQL, [like, like, like]):
        late = float(r["delay_d"] or 0)
        hits.append({
            "kind": "run", "tag": r["tag"], "section": r["section"],
            "ref": r["reference"],
            "main": r["item_id"] or r["reference"],
            "sub": "run in %s · %s%s" % (
                r["section"], r["tag"],
                (" · %.1f d late" % late) if late > LATE_EPS else ""),
            "path": [r["tag"], r["section"]],
        })

    return JsonResponse({"q": q, "hits": hits[:40]})


# Which shipments each blocking machine actually holds, worst first -
# so a click on the rail can take the planner straight there instead of
# only tinting the cards.
BLOCK_SHIPS_SQL = """
select c.owner as machine,
       coalesce(nullif(d.category, ''), d.name) as tag,
       count(distinct c.demand) as orders
from out_constraint c
join demand d on d.name = c.demand
where c.name = 'overload'
group by 1, 2
order by 1, 3 desc
"""

# ------------------------------------------------------- open everything
#
# "Open all" used to fire one request per section - thirty round trips
# for a big shipment, each followed by a full re-layout, which took
# minutes and looked like the page had hung. This returns the whole
# shipment in one query instead.
#
# ALL_RUNS_CAP bounds it. A shipment can carry 2,148 runs across 30
# sections; past a few hundred cards "open all" stops being a picture
# anyone reads, and the response says how many it left out rather than
# truncating silently.
ALL_RUNS_CAP = 900

ALL_RUNS_SQL = _MO + """,
steps as (
  select w.owner_id as run,
         coalesce(r.owner_id, opr.resource_id) as section,
         string_agg(distinct opr.resource_id, ', ') as machines,
         count(*) as nsteps
  from operationplan w
  join operationplanresource opr on opr.operationplan_id = w.reference
  join resource r on r.name = opr.resource_id
  where w.type = 'WO' and w.owner_id in (select reference from mo)
  group by 1, 2)
select s.section, m.reference, m.item_id,
       coalesce(i.description, '') as item_descr,
       m.quantity, m.startdate, m.enddate,
       round((extract(epoch from m.delay)/86400.0)::numeric, 1) as delay_d,
       m.criticality, s.machines, s.nsteps,
       (select count(distinct coalesce(nullif(d2.category, ''), d2.name))
        from jsonb_object_keys(coalesce(m.plan->'pegging', '{}'::jsonb)) k(d)
        join demand d2 on d2.name = k.d) as ships
from mo m
join steps s on s.run = m.reference
left join item i on i.name = m.item_id
order by m.delay desc nulls last, m.enddate desc
"""


@login_required(login_url="/data/login/")
def all_runs_data(request, tag):
    """Every section's runs for one shipment, in a single round trip."""
    db = _db(request)
    rows = _rows(db, ALL_RUNS_SQL, [tag])

    by_section = {}
    kept = 0
    dropped = 0
    for r in rows:
        if kept >= ALL_RUNS_CAP:
            dropped += 1
            continue
        kept += 1
        delay = float(r["delay_d"] or 0)
        ships = int(r["ships"] or 1)
        sec = r["section"]
        by_section.setdefault(sec, []).append({
            "id": "R:" + tag + ":" + sec + ":" + r["reference"],
            "ref": r["reference"],
            "kind": "run",
            "tag": tag,
            "section": sec,
            "item": r["item_id"] or "",
            "descr": r["item_descr"],
            "qty": _num(r["quantity"]),
            "start": _iso(r["startdate"]),
            "end": _iso(r["enddate"]),
            "start_txt": _fmt(r["startdate"]),
            "end_txt": _fmt(r["enddate"]),
            "delay_days": delay,
            "machines": r["machines"] or "",
            "steps": r["nsteps"],
            "ships": ships,
            "shared": ships > 1,
            "tone": _tone(delay, r["criticality"]),
        })

    return JsonResponse({
        "tag": tag,
        "sections": [{"parent": "G:" + tag + ":" + k, "section": k, "nodes": v}
                     for k, v in by_section.items()],
        "shown": kept,
        "dropped": dropped,
        "cap": ALL_RUNS_CAP,
    })
