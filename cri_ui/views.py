#
# The guided home screen.
#
# frePPLe's own cockpit is a wall of KPI tiles - it shows everything at once
# and implies no order of work. This replaces it with the five steps a
# Sieger planner actually walks through, in sequence, each with a live
# number and one action.
#

import zlib
from datetime import datetime, timedelta
from itertools import groupby
from urllib.parse import quote, urlencode

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS
from django.db.models import Count
from django.db.models.expressions import RawSQL
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from freppledb.common.models import Parameter
from freppledb.execute.models import Task
from freppledb.input.models import (
    CalendarBucket,
    Demand,
    Item,
    Location,
    Operation,
    OperationPlan,
    OperationPlanMaterial,
    OperationPlanResource,
    Resource,
)
from freppledb.output.models import Constraint, Problem


def _rows(db, sql, params=None):
    """Raw SQL to dicts - local twin of reports._rows, kept here so the
    import graph stays acyclic (reports imports helpers from views)."""
    from django.db import connections

    with connections[db].cursor() as c:
        c.execute(sql, params or [])
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


def _ship_names(db):
    """
    DSQ tag -> human name ('Project Alpha'), from the 'sieger.shipnames'
    parameter the loader writes off the workbook's DSQ sheet.

    Falls back to the tag alone - the product master's descriptions are
    machine codes (PRODUCT_...) and planners asked never to see those.
    A DSQ the sheet doesn't name simply shows its own code.
    """
    import json

    try:
        val = Parameter.objects.using(db).get(
            name="sieger.shipnames").value
        data = json.loads(val) if val else {}
        return {
            t: (v.get("desc") or t) if isinstance(v, dict) else (v or t)
            for t, v in data.items()
        }
    except Exception:
        return {}


def _plan_now(db):
    """
    The plan's current date - pinned in the 'currentdate' parameter for
    reproducible runs, falling back to wall-clock if it isn't set.
    """
    try:
        val = Parameter.objects.using(db).get(name="currentdate").value
        if val:
            return timezone.datetime.fromisoformat(val.strip().replace(" ", "T"))
    except Exception:
        pass
    return timezone.now().replace(tzinfo=None)


# ---- the shipment summary on the home screen ---------------------------
# Everything derives from tables that reload with every plan, so a new
# DSQ appears on the home screen the moment it carries demand rows.
COCKPIT_SHIPS_SQL = """
select coalesce(nullif(d.category, ''), d.name) as tag,
       min(d.subcategory) as project,
       max(i.description) as descr,
       count(*) as orders,
       count(*) filter (where d.deliverydate is null) as unplanned,
       count(*) filter (
          where d.deliverydate is not null and d.deliverydate <= d.due
        ) as on_time,
       count(*) filter (where d.deliverydate > d.due) as late_orders,
       count(*) filter (where d.deliverydate is not null and
         (d.plannedquantity is null or d.plannedquantity < d.quantity)) as short_orders,
       min(d.due) as due,
       max(d.deliverydate) as finish,
       max(d.delay) as worst
from demand d
left join item i on i.name = coalesce(nullif(d.category, ''), d.name)
where d.status in ('open', 'quote')
group by 1
"""

# Machine hours per shipment through the engine's own pegging; hours
# count once per (work order, shipment). ok_h = hours on work whose
# pegged production orders of that shipment still meet their target.
COCKPIT_HOURS_SQL = """
with peg as (
  select coalesce(nullif(category, ''), name) as tag, name as ord,
         deliverydate is not null and delay <= interval '0' as ok
  from demand where status in ('open', 'quote')
)
select tag, min(startdate) as first_cut, sum(h) as total_h, sum(ok_h) as ok_h
from (
  select op.reference, p.tag, op.startdate,
          greatest(0, extract(epoch from (op.enddate - op.startdate))
            - coalesce((op.plan->>'unavailable')::numeric, 0)) / 3600.0 as h,
         case when bool_or(p.ok)
               then greatest(0, extract(epoch from (op.enddate - op.startdate))
                 - coalesce((op.plan->>'unavailable')::numeric, 0)) / 3600.0
              else 0 end as ok_h
  from operationplan op
  join lateral jsonb_object_keys(
       coalesce(op.plan->'pegging', '{}'::jsonb)) k(d) on true
  join peg p on p.ord = k.d
  where op.type = 'WO'
   group by 1, 2, op.startdate, op.enddate, op.plan
) t
group by 1
"""

COCKPIT_CONSTR_SQL = """
select coalesce(nullif(d.category, ''), d.name) as tag,
       c.name as kind, c.owner,
       count(distinct c.demand) as orders
from out_constraint c
join demand d on d.name = c.demand
where d.status in ('open', 'quote')
group by 1, 2, 3
"""


def _cockpit_ships(db, now):
    """
    Per-shipment rollup for the home screen: dates, on-target share,
    machine hours, the engine's constraint record and one action line.
    The list IS the demand table's shipment tags - nothing hard-coded.
    """
    hours = {r["tag"]: r for r in _rows(db, COCKPIT_HOURS_SQL)}
    names = _ship_names(db)
    res_desc = {
        r["name"]: r["description"] or ""
        for r in Resource.objects.using(db).values("name", "description")
    }
    item_desc = {
        r["name"]: r["description"] or ""
        for r in Item.objects.using(db).values("name", "description")
    }

    constr = {}
    for r in _rows(db, COCKPIT_CONSTR_SQL):
        kind, owner = r["kind"], r["owner"] or ""
        if kind == "overload":
            label = "Capacity: %s" % (res_desc.get(owner) or owner)
        elif "purchasing" in kind:
            part = owner.split()[1] if len(owner.split()) > 1 else owner
            label = "Material: %s" % (item_desc.get(part) or part)
        elif "manufacturing" in kind:
            part = owner[3:] if owner.startswith("RT-") else owner
            label = "Long routing: %s" % (item_desc.get(part) or part)
        else:
            label = kind
        c = constr.setdefault(r["tag"], {})
        c[label] = c.get(label, 0) + r["orders"]

    out = []
    for r in _rows(db, COCKPIT_SHIPS_SQL):
        h = hours.get(r["tag"], {})
        due, finish = r["due"], r["finish"]
        first_cut = h.get("first_cut")
        flow = (finish - first_cut).days if (finish and first_cut) else None
        start_by = (due - timedelta(days=flow)) if (due and flow) else None
        worst = r["worst"].total_seconds() / 86400.0 if r["worst"] else 0.0
        pct = round(100.0 * r["on_time"] / r["orders"]) if r["orders"] else 0

        issues = sorted(constr.get(r["tag"], {}).items(),
                        key=lambda kv: -kv[1])[:3]
        if r["late_orders"] or r["unplanned"]:
            action = ("%d late / %d unplanned - inspect delivery evidence"
                      % (r["late_orders"], r["unplanned"]))
        elif r["short_orders"]:
            action = "Planned quantity incomplete - inspect delivery evidence"
        else:
            action = "On time in the saved plan - review progress"

        out.append({
            "tag": r["tag"],
            "name": names.get(r["tag"]) or r["tag"],
            "project": r["project"] or "",
            "orders": r["orders"],
            "on_time": r["on_time"],
            "pct": pct,
            "unplanned": r["unplanned"],
            "late_orders": r["late_orders"],
            "short_orders": r["short_orders"],
            "due": due,
            "week": due.isocalendar()[1] if due else None,
            "finish": finish,
            "first_cut": first_cut,
            "start_by": start_by,
            "worst_d": round(worst, 1),
            "total_h": float(h.get("total_h") or 0),
            "ok_h": float(h.get("ok_h") or 0),
            "ok_hpct": (round(100.0 * float(h.get("ok_h") or 0) /
                        float(h["total_h"])) if h.get("total_h") else 0),
            "issues": issues,
            "action": action,
        })
    out.sort(key=lambda s: (s["due"] is None, s["due"] or now))
    return out


@login_required(login_url="/data/login/")
def cockpit(request):
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    now = _plan_now(db)

    ships = _cockpit_ships(db, now)
    total_orders = sum(s["orders"] for s in ships)
    total_h = sum(s["total_h"] for s in ships)
    ok_h = sum(s["ok_h"] for s in ships)
    late_ships = sum(1 for s in ships if s["late_orders"] or s["unplanned"] or s["short_orders"])

    last_run = (
        Task.objects.using(db)
        .filter(name="runplan", status="Done", finished__isnull=False,
                arguments__regex=r"(^|[=, ])supply([, ]|$)")
        .order_by("-id")
        .values("started", "finished", "status")
        .first()
    )

    return render(
        request,
        "sieger_ui/cockpit.html",
        context={
            "title": _("planning"),
            "now": now,
            "ships": ships,
            "total_orders": total_orders,
            "total_h": round(total_h),
            "ok_h": round(ok_h),
            "ok_hpct": round(100.0 * ok_h / total_h) if total_h else 0,
            "late_ships": late_ships,
            "last_run": last_run,
        },
    )


# ---- one shipment, everything about it ------------------------------
SHIP_GROUPS_SQL = """
select grp, max(descr) as descr, count(*) as ops, sum(hours) as hours
from (
select distinct op.reference, coalesce(r.owner_id, opr.resource_id) as grp,
       g.description as descr,
        greatest(0, extract(epoch from (op.enddate - op.startdate))
          - coalesce((op.plan->>'unavailable')::numeric, 0)) / 3600.0 as hours
from operationplan op
join operationplanresource opr on opr.operationplan_id = op.reference
join resource r on r.name = opr.resource_id
left join resource g on g.name = coalesce(r.owner_id, opr.resource_id)
where op.type = 'WO' and op.plan->'pegging' ?| (
  select coalesce(array_agg(d.name), array[]::varchar[]) from demand d
  where coalesce(nullif(d.category, ''), d.name) = coalesce(%s, nullif(d.category, ''), d.name)
    and d.status in ('open', 'quote'))
) linked
group by grp
order by hours desc
"""

@login_required(login_url="/data/login/")
def shipment_detail(request, tag=None):
    """Shipment-scoped delivery evidence from the selected scenario's saved plan."""
    from .shipment_analysis import shipment_analysis

    prefix = getattr(request, "prefix", "")
    if tag is None:
        selected = request.GET.get("dsq")
        return HttpResponseRedirect(
            prefix + "/shipment/" + quote(selected, safe="") + "/"
            if selected else prefix + "/"
        )
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    context = shipment_analysis(db, tag, _rows, timezone.now(), prefix)
    if not context["orders"]:
        raise Http404("No open or quote orders exist for this shipment.")
    names = _ship_names(db)
    ships = _rows(db, """
        select distinct coalesce(nullif(category, ''), name) as tag
        from demand where status in ('open', 'quote') order by 1
    """)
    for ship in ships:
        ship["name"] = names.get(ship["tag"]) or ship["tag"]
    dates = [o["due"] for o in context["orders"] if o["due"]]
    return render(
        request, "sieger_ui/shipment.html", context={
            **context, "title": _("shipment analysis"), "ships": ships,
            "selected_shipment": tag, "plan_now": _plan_now(db),
            "s": {"tag": tag, "name": names.get(tag) or tag,
                  "due": min(dates) if dates else None},
            "groups": _rows(db, SHIP_GROUPS_SQL, [tag]),
        },
    )


@login_required(login_url="/data/login/")
def production_orders(request):
    """Server-filtered production orders; never load the full shipment analysis."""
    from .production_orders import order_list_context

    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    context = order_list_context(
        db, request.GET, _ship_names(db), getattr(request, "prefix", "")
    )
    return render(
        request, "sieger_ui/production_orders.html",
        context={"title": _("production orders"), **context},
    )


# ======================================================================
# The machine schedule.
#
# WHY THIS EXISTS AND NOT frePPLe'S OWN GANTT
# -------------------------------------------
# frePPLe's AngularJS Gantt was unreadable on this plan, for two reasons
# that no amount of styling could reach:
#
#  1. It lane-packs on the CLIENT, greedily, and only reuses a lane when
#     the next bar starts after that lane's last one ends. That is only
#     correct if the bars arrive sorted by start date - and they arrive
#     sorted by database id, because ResourceDetail inherits
#     default_sort = (0, "asc") and its column 0 is `id`. Out of order,
#     nearly every bar opens a new lane. BSC-RMY-02 rendered about 23
#     lanes tall. Its true peak concurrency is 2.
#
#  2. It draws the whole planning horizon at once. Sieger's plan spans
#     two months while the average operation runs four hours, so every
#     bar came out roughly three pixels wide.
#
# Packing here happens in Python over a queryset sorted by start date, so
# it is minimal by construction, and the window defaults to a week, so a
# four-hour job is a readable block rather than a hairline. Geometry is
# emitted as percentages and the page is plain server-rendered HTML - no
# client-side layout to go wrong, no framework, and it stays correct when
# the browser is resized.
# ======================================================================

MAX_WINDOW_DAYS = 90
# Two weeks. Sieger's plan is sparse in its first week - a 7-day default
# showed 3 of 15 loaded machines, 14 days shows 5 with the day columns
# still individually labelled. Beyond ~21 days the header goes weekly.
DEFAULT_WINDOW_DAYS = 14


def _window(request, now):
    """Visible window, read from the query string with sane defaults."""
    try:
        days = int(request.GET.get("days", DEFAULT_WINDOW_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_WINDOW_DAYS
    days = max(1, min(days, MAX_WINDOW_DAYS))

    start = None
    raw = request.GET.get("start")
    if raw:
        try:
            start = datetime.fromisoformat(raw)
        except ValueError:
            start = None
    if start is None:
        start = now
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=days), days


def _pack(bars):
    """
    Greedy interval partitioning. Input MUST be sorted by start date -
    the queryset's order_by guarantees that - and it then uses the fewest
    lanes possible, which is exactly the machine's peak concurrency.
    """
    lane_ends = []
    for bar in bars:
        placed = False
        for i, lane_end in enumerate(lane_ends):
            if lane_end <= bar["start"]:
                lane_ends[i] = bar["end"]
                bar["lane"] = i
                placed = True
                break
        if not placed:
            bar["lane"] = len(lane_ends)
            lane_ends.append(bar["end"])
    return max(1, len(lane_ends))


def _busy_seconds(bars):
    """
    Union of the bar intervals inside the window. Overlap is counted once,
    so a machine can never report more than 100% utilisation.
    """
    total = 0.0
    cur_s = cur_e = None
    for bar in sorted(bars, key=lambda b: b["vstart"]):
        s, e = bar["vstart"], bar["vend"]
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        elif e > cur_e:
            cur_e = e
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total


_WEEKDAY_FIELDS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _nonworking_weekdays(db):
    """
    Which weekdays the plant does NOT work, read from the shift calendar.

    The tint on the plan-board header used to be a hard-coded Gregorian
    weekend (Sat+Sun). That disagreed with the plan: the loaded calendar
    runs Mon-Sat with only Sunday off, so Saturday was shaded as idle
    while the solver was booking work into it - bars in a grey column,
    which reads as a bug to anyone looking at the board.

    Reading it from CalendarBucket means the shading follows the
    calendar. When LN supplies real per-machine patterns, or a shutdown
    week is added, the board updates with no code change - the same
    principle applied to /bottlenecks/ reading real capacity.

    If the location carries no usable pattern (a permissive or empty
    calendar - it sat on a bucket-less VENDOR-24X7 once, and every
    weekend got shaded though the plant works Saturdays), the weekdays
    are read from the MACHINES' own calendars instead: a weekday is
    working when a majority of machines work it. Only when there are no
    machine calendars at all does the Gregorian {Sat, Sun} guess apply.
    """
    working = set()
    try:
        cal = (
            Location.objects.using(db)
            .exclude(available__isnull=True)
            .values_list("available_id", flat=True)
            .first()
        )
        rows = CalendarBucket.objects.using(db).filter(value__gt=0)
        if cal:
            rows = rows.filter(calendar_id=cal)
        for b in rows.values(*_WEEKDAY_FIELDS):
            for i, field in enumerate(_WEEKDAY_FIELDS):
                if b[field]:
                    working.add(i)
    except Exception:
        # A missing or half-loaded calendar must not take the board down.
        working = set()
    if not working:
        try:
            from collections import Counter

            weights = Counter(
                Resource.objects.using(db)
                .filter(type="default")
                .exclude(available=None)
                .values_list("available_id", flat=True)
            )
            counts = [0] * 7
            seen = set()
            for b in (
                CalendarBucket.objects.using(db)
                .filter(value__gt=0, calendar_id__in=list(weights))
                .values("calendar_id", *_WEEKDAY_FIELDS)
            ):
                for i, field in enumerate(_WEEKDAY_FIELDS):
                    if b[field] and (b["calendar_id"], i) not in seen:
                        seen.add((b["calendar_id"], i))
                        counts[i] += weights[b["calendar_id"]]
            total = sum(weights.values())
            if total:
                working = {i for i in range(7) if counts[i] * 2 > total}
        except Exception:
            pass
    if not working:
        return {5, 6}
    return {i for i in range(7) if i not in working}


def _ticks(start, end, days, db=None):
    """Header columns. Daily while they still fit, weekly beyond that."""
    offdays = _nonworking_weekdays(db) if db else {5, 6}
    out = []
    span = (end - start).total_seconds()
    step = 1 if days <= 21 else 7
    d = start
    if step == 7:
        d -= timedelta(days=d.weekday())
    while d < end:
        if d >= start:
            out.append(
                {
                    "left": round(100.0 * (d - start).total_seconds() / span, 4),
                    "width": round(100.0 * step * 86400 / span, 4),
                    "label": d.strftime("%a %d" if step == 1 else "%d %b"),
                    "month": d.strftime("%b"),
                    "major": d.weekday() == 0,
                    "weekend": step == 1 and d.weekday() in offdays,
                }
            )
        d += timedelta(days=step)
    return out


def _hierarchy(db):
    """
    The whole resource tree in one query: section (lvl 0) -> machine
    group (lvl 1) -> machine (lvl 2), per loader/load_planning.py. Some
    groups hold exactly one machine and are loaded as a lvl-1 leaf that
    carries the capacity itself - for those the machine IS its group.

    Returns (nodes, ancestry) where ancestry(name) -> (section, group),
    either of which may be None when the chain does not resolve.
    """
    nodes = {
        r["name"]: r
        for r in Resource.objects.using(db).values(
            "name", "owner_id", "lvl", "description"
        )
    }

    def ancestry(name):
        chain, seen = [], set()
        node = nodes.get(name)
        while node and node["name"] not in seen:
            seen.add(node["name"])
            chain.append(node)
            node = nodes.get(node["owner_id"]) if node["owner_id"] else None
        if len(chain) < 2:
            # No owner at all - a vendor pool or an unparented machine.
            return None, name
        # chain runs machine -> ... -> root. The root is the section, the
        # node just under it is the group - which is the machine itself
        # when the group is a self-named lvl-1 leaf.
        return chain[-1]["name"], chain[-2]["name"]

    return nodes, ancestry


@login_required(login_url="/data/login/")
def schedule(request):
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    now = _plan_now(db)
    start, end, days = _window(request, now)
    span = (end - start).total_seconds()

    nodes, ancestry = _hierarchy(db)
    cur_section = request.GET.get("section") or ""
    cur_group = request.GET.get("group") or ""
    cur_demand = request.GET.get("demand") or ""
    # Shipment (DSQ) narrowing: resolve each order to its shipment tag
    # once, then keep only bars whose work serves that shipment.
    cur_dsq = request.GET.get("dsq") or ""
    dsq_map = {}
    if cur_dsq:
        dsq_map = {
            n: c
            for n, c in Demand.objects.using(db)
            .filter(status__in=["open", "quote"])
            .exclude(category=None)
            .values_list("name", "category")
        }

    # Section / group narrowing happens in the database: precompute the
    # allowed machine set from the (tiny) hierarchy and filter on it.
    allowed = None
    if cur_section or cur_group:
        allowed = [
            name
            for name in nodes
            if (
                (not cur_section or ancestry(name)[0] == cur_section)
                and (not cur_group or ancestry(name)[1] == cur_group)
            )
        ]

    # Sorted by resource, then by start date. That second key is the whole
    # point - see _pack().
    rows = (
        OperationPlanResource.objects.using(db)
        .filter(
            operationplan__startdate__lt=end,
            operationplan__enddate__gte=start,
        )
        # The customer order a bar serves. demand_id is only set on
        # delivery rows; for a WO/MO the engine records it in the pegging
        # json instead, so pull the heaviest pegged demand as a fallback.
        # Annotated BEFORE .values() so no group-by semantics can kick in.
        .annotate(
            pegged=RawSQL(
                "(select key from jsonb_each_text(operationplan.plan->'pegging')"
                " order by value::numeric desc limit 1)",
                (),
            )
        )
        .values(
            "resource_id",
            "operationplan__reference",
            "operationplan__startdate",
            "operationplan__enddate",
            "operationplan__status",
            "operationplan__type",
            "operationplan__quantity",
            "operationplan__delay",
            "operationplan__criticality",
            "operationplan__item__name",
            "operationplan__operation__name",
            "operationplan__demand__name",
            "operationplan__owner__demand__name",
            "pegged",
        )
        .order_by("resource_id", "operationplan__startdate", "operationplan__enddate")
    )
    if allowed is not None:
        rows = rows.filter(resource_id__in=allowed)

    machines = []
    late_total = 0
    bar_total = 0
    demand_counts = {}

    for resource, group in groupby(rows, key=lambda r: r["resource_id"]):
        bars = []
        for r in group:
            s = r["operationplan__startdate"]
            e = r["operationplan__enddate"]
            if s is None:
                continue
            if e is None or e < s:
                e = s
            vs, ve = max(s, start), min(e, end)
            if ve < vs:
                ve = vs

            # Resolve the customer order: the direct FK (deliveries), the
            # heaviest pegged demand (most WO/MO bars), or the parent
            # job's FK - in that order.
            demand = (
                r["operationplan__demand__name"]
                or r["pegged"]
                or r["operationplan__owner__demand__name"]
                or ""
            )
            if demand:
                demand_counts[demand] = demand_counts.get(demand, 0) + 1
            # The demand filter runs here, in Python, over the RESOLVED
            # order - the options list above stays complete either way.
            if cur_demand and demand != cur_demand:
                continue
            # Shipment filter: same place, one hop further up the pegging.
            if cur_dsq and dsq_map.get(demand, "") != cur_dsq:
                continue

            delay = r["operationplan__delay"]
            delay_days = delay.total_seconds() / 86400.0 if delay else 0.0
            crit = r["operationplan__criticality"] or 0
            if crit >= 999:
                tone = "crit"
            elif delay_days > 0.05:
                tone = "late"
            else:
                tone = "ok"
            if tone != "ok":
                late_total += 1

            bars.append(
                {
                    "ref": r["operationplan__reference"],
                    "label": (
                        r["operationplan__item__name"]
                        or r["operationplan__operation__name"]
                        or r["operationplan__reference"]
                    ),
                    "operation": r["operationplan__operation__name"] or "",
                    "item": r["operationplan__item__name"] or "",
                    "demand": demand,
                    # Stable per-order hue for the "colour by order" mode.
                    "hue": zlib.crc32(demand.encode()) % 360 if demand else 200,
                    "type": r["operationplan__type"] or "",
                    "status": (r["operationplan__status"] or "").lower(),
                    "qty": r["operationplan__quantity"],
                    "start": s,
                    "end": e,
                    "vstart": vs,
                    "vend": ve,
                    "hours": round((e - s).total_seconds() / 3600.0, 2),
                    "delay_days": round(delay_days, 1),
                    "tone": tone,
                    "left": round(100.0 * (vs - start).total_seconds() / span, 4),
                    "width": round(100.0 * (ve - vs).total_seconds() / span, 4),
                    "clipped": s < start or e > end,
                }
            )

        if not bars:
            continue
        lanes = _pack(bars)
        bar_total += len(bars)
        machines.append(
            {
                "name": resource,
                # The human name beside the code - planners read
                # "BANDSAW CUTTING-1", not "BSC-RMY-01".
                "descr": (nodes.get(resource) or {}).get("description") or "",
                "bars": bars,
                "lanes": lanes,
                "count": len(bars),
                "util": round(100.0 * _busy_seconds(bars) / span, 1) if span else 0.0,
                "late": sum(1 for b in bars if b["tone"] != "ok"),
            }
        )

    # Busiest first - that is the order a planner triages in.
    machines.sort(key=lambda m: (-m["util"], m["name"]))

    # ---- fold the flat machine list into section -> group -> machines.
    # Machines keep their busiest-first order inside a group; groups are
    # ordered by their busiest machine; sections alphabetically with the
    # unresolvable leftovers last.
    sec_map = {}
    for m in machines:
        sec, grp = ancestry(m["name"])
        sec = sec or "other"
        grp = grp or m["name"]
        s = sec_map.setdefault(
            sec,
            {"code": sec, "label": "", "groups": {}, "count": 0,
             "late": 0, "bar_count": 0, "utils": []},
        )
        g = s["groups"].setdefault(grp, {"code": grp, "label": "", "machines": []})
        g["machines"].append(m)
        s["count"] += 1
        s["late"] += m["late"]
        s["bar_count"] += m["count"]
        s["utils"].append(m["util"])

    sections = []
    for code in sorted(sec_map, key=lambda c: (c == "other", c)):
        s = sec_map[code]
        node = nodes.get(code)
        s["label"] = (node["description"] or "") if node else ""
        groups = list(s["groups"].values())
        for g in groups:
            gnode = nodes.get(g["code"])
            g["label"] = (gnode["description"] or "") if gnode else ""
        groups.sort(key=lambda g: (-max(m["util"] for m in g["machines"]), g["code"]))
        s["groups"] = groups
        s["multi_group"] = len(groups) > 1
        s["avg_util"] = round(sum(s["utils"]) / len(s["utils"]), 1) if s["utils"] else 0.0
        del s["utils"]
        sections.append(s)

    # The order filter dropdown: the busiest orders in this window.
    demand_options = sorted(demand_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:30]
    if cur_demand and cur_demand not in demand_counts:
        demand_options.insert(0, (cur_demand, 0))

    # Section chips: every root that actually owns machines, whether it
    # is loaded in this window or not, so the filter is always reachable.
    # (Deliberately not keyed on lvl - frePPLe only maintains that column
    # when its hierarchy rebuild has run.)
    owners = {n["owner_id"] for n in nodes.values() if n["owner_id"]}
    section_chips = sorted(
        n["name"]
        for n in nodes.values()
        if n["owner_id"] is None and n["name"] in owners
    )

    now_pct = None
    if start <= now < end:
        now_pct = round(100.0 * (now - start).total_seconds() / span, 4)

    # Query-string fragment every navigation link must carry so the
    # filters survive paging through time.
    keep = ""
    if cur_section:
        keep += "&section=%s" % cur_section
    if cur_group:
        keep += "&group=%s" % cur_group
    if cur_demand:
        keep += "&demand=%s" % cur_demand
    if cur_dsq:
        keep += "&dsq=%s" % cur_dsq

    # Every shipment in the book, human name and code side by side, so
    # the dropdown reads for planners and stays complete as DSQs arrive.
    ship_names = _ship_names(db)
    dsq_options = [
        (c, ship_names.get(c) or c)
        for c in (
            Demand.objects.using(db)
            .filter(status__in=["open", "quote"])
            .exclude(category=None)
            .values_list("category", flat=True)
            .distinct()
            .order_by("category")
        )
    ]

    # The drag-and-drop move banner - state of the last pin-and-replan.
    # Imported here rather than at module top to keep the two modules'
    # import graph acyclic (moves.py imports helpers from this file).
    from .moves import banner_context

    context = {
            "title": _("machine schedule"),
            "now": now,
            "start": start,
            "end": end,
            "days": days,
            "ticks": _ticks(start, end, days, db),
            "machines": machines,
            "sections": sections,
            "section_chips": section_chips,
            "cur_section": cur_section,
            "cur_group": cur_group,
            "cur_demand": cur_demand,
            "demand_options": demand_options,
            "cur_dsq": cur_dsq,
            "dsq_options": dsq_options,
            "keep": keep,
            "now_pct": now_pct,
            "bar_total": bar_total,
            "late_total": late_total,
            "idle_machines": Resource.objects.using(db).count() - len(machines),
            "prev_start": (start - timedelta(days=days)).date().isoformat(),
            "next_start": (start + timedelta(days=days)).date().isoformat(),
            "today_start": now.date().isoformat(),
            "ranges": (1, 3, 7, 14, 30),
            "span_seconds": int(span),
            "can_move": request.user.has_perm("input.change_manufacturingorder")
            and request.user.has_perm("auth.generate_plan"),
    }
    context.update(banner_context(request, db))
    return render(request, "sieger_ui/schedule.html", context=context)


# ======================================================================
# One operation, in full, as JSON - the plan board's click-through.
#
# The board itself stays server-rendered and framework-free, so this is
# fetched on click rather than shipping every field for all 4,500 bars up
# front. Everything a planner asks after "why is this bar here?" lives in
# one payload: the routing it belongs to, the machines, the components,
# the customer order it ultimately feeds, and - when that order is late -
# the reason the engine recorded.
# ======================================================================


def _fmt(dt):
    """USE_TZ is False throughout frePPLe, so these are already local."""
    return dt.strftime("%a %d %b %Y, %H:%M") if dt else ""


def _num(v):
    """Decimal -> the shortest honest string: 50, 12.5, 0.25."""
    if v is None:
        return ""
    f = float(v)
    if f == int(f):
        return str(int(f))
    return ("%.3f" % f).rstrip("0").rstrip(".")


def _days(td):
    return round(td.total_seconds() / 86400.0, 1) if td else 0.0


def _job_refs(db, refs):
    """Expand operationplan references to their whole job: parent + steps."""
    if not refs:
        return set()
    out = set(refs)
    for o in (
        OperationPlan.objects.using(db)
        .filter(reference__in=list(refs))
        .values("reference", "owner_id")
    ):
        if o["owner_id"]:
            out.add(o["owner_id"])
    out |= set(
        OperationPlan.objects.using(db)
        .filter(owner_id__in=list(out))
        .values_list("reference", flat=True)
    )
    return out


def _orders_downstream(db, produced, limit=8, maxdepth=6):
    """
    Which customer orders does this operation ultimately feed?

    Follows the real material flow rather than guessing by item: whatever
    this job produces into a buffer, find what later consumes it, and
    repeat until a delivery is reached - a delivery carries the demand.
    Bounded on both depth and breadth so a popup can never run away.
    """
    found = []
    seen_flow = set()
    frontier = [(p["item_id"], p["location_id"], p["flowdate"]) for p in produced]

    for _ in range(maxdepth):
        if not frontier or len(found) >= limit:
            break
        sinks = set()
        for item, loc, when in frontier:
            key = (item, loc, when)
            if key in seen_flow:
                continue
            seen_flow.add(key)
            sinks.update(
                OperationPlanMaterial.objects.using(db)
                .filter(
                    item_id=item,
                    location_id=loc,
                    quantity__lt=0,
                    flowdate__gte=when,
                )
                .order_by("flowdate")
                .values_list("operationplan_id", flat=True)[:limit]
            )
        if not sinks:
            break

        unresolved = set()
        for o in (
            OperationPlan.objects.using(db)
            .filter(reference__in=list(sinks))
            .values("reference", "demand_id")
        ):
            if o["demand_id"]:
                if o["demand_id"] not in found:
                    found.append(o["demand_id"])
            else:
                unresolved.add(o["reference"])

        # Same (item, location, when) tuples the loop unpacks. This was
        # a list of DICTS once - tuple-unpacking a dict silently yields
        # its key names, so hop two queried flowdate >= "flowdate" and
        # every multi-hop popup answered HTTP 500.
        frontier = [
            (f["item_id"], f["location_id"], f["flowdate"])
            for f in OperationPlanMaterial.objects.using(db)
            .filter(operationplan_id__in=list(_job_refs(db, unresolved)), quantity__gt=0)
            .values("item_id", "location_id", "flowdate")
        ]
    return found[:limit]


@login_required(login_url="/data/login/")
def operation_detail(request, ref):
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    prefix = getattr(request, "prefix", "") or ""

    try:
        op = (
            OperationPlan.objects.using(db)
            .select_related("item", "operation")
            .get(reference=ref)
        )
    except OperationPlan.DoesNotExist:
        raise Http404("no such operation")

    delay_days = _days(op.delay)
    crit = float(op.criticality or 0)
    tone = "crit" if crit >= 999 else ("late" if delay_days > 0.05 else "ok")

    # -- the job, and every step in its routing --------------------------
    # A routing explodes into one MO parent plus one WO child per step.
    # Listing the siblings is what turns "a bar" into "step 2 of 5".
    parent_ref = op.owner_id or op.reference
    parent = (
        op
        if parent_ref == op.reference
        else OperationPlan.objects.using(db).filter(reference=parent_ref).first() or op
    )
    steps = list(
        OperationPlan.objects.using(db)
        .filter(owner_id=parent_ref)
        .order_by("startdate", "reference")
        .values(
            "reference",
            "operation_id",
            "startdate",
            "enddate",
            "status",
            "quantity",
            "delay",
        )
    )

    # -- machines, for this step and every sibling -----------------------
    all_refs = {op.reference, parent_ref} | {s["reference"] for s in steps}
    res_by_op = {}
    for r in (
        OperationPlanResource.objects.using(db)
        .filter(operationplan_id__in=list(all_refs))
        .values("operationplan_id", "resource_id", "quantity", "setup")
        .order_by("resource_id")
    ):
        res_by_op.setdefault(r["operationplan_id"], []).append(
            {
                "name": r["resource_id"],
                "quantity": _num(r["quantity"]),
                "setup": r["setup"] or "",
            }
        )

    # -- components in, product out --------------------------------------
    # Consumption stays empty until the LN bill of materials is loaded;
    # the section is built to fill itself the moment it is.
    consumes, produces, produced_raw = [], [], []
    for m in (
        OperationPlanMaterial.objects.using(db)
        .filter(operationplan_id__in=list(all_refs))
        .order_by("flowdate")
        .values(
            "operationplan_id",
            "item_id",
            "item__description",
            "location_id",
            "quantity",
            "flowdate",
            "onhand",
        )
    ):
        row = {
            "item": m["item_id"],
            "description": m["item__description"] or "",
            "location": m["location_id"] or "",
            "quantity": _num(abs(m["quantity"]) if m["quantity"] else 0),
            "date": _fmt(m["flowdate"]),
            "onhand": _num(m["onhand"]),
            "step": m["operationplan_id"],
        }
        if float(m["quantity"] or 0) > 0:
            produces.append(row)
            produced_raw.append(m)
        else:
            consumes.append(row)

    # -- the customer orders this feeds, and why they slip ---------------
    order_names = _orders_downstream(db, produced_raw)
    reasons = {}
    if order_names:
        for c in (
            Constraint.objects.using(db)
            .filter(demand__in=order_names)
            .order_by("startdate")
            .values("demand", "name", "owner", "description", "startdate", "enddate")
        ):
            reasons.setdefault(c["demand"], []).append(
                {
                    "name": c["name"],
                    "owner": c["owner"],
                    "description": c["description"],
                    "start": _fmt(c["startdate"]),
                    "end": _fmt(c["enddate"]),
                }
            )

    orders = []
    for d in (
        Demand.objects.using(db)
        .filter(name__in=order_names)
        .values(
            "name",
            "item_id",
            "customer_id",
            "due",
            "quantity",
            "deliverydate",
            "delay",
            "status",
            "priority",
        )
    ):
        orders.append(
            {
                "name": d["name"],
                # The project timeline for this order: every operation the
                # plan does for it, on ONE axis fitted to the order's own
                # span. This is the way out of paging the board window by
                # window to follow a job - see timeline.project_timeline.
                "trace": "%s/projects/%s/" % (prefix, quote(str(d["name"]))),
                "item": d["item_id"],
                "customer": d["customer_id"] or "",
                "due": _fmt(d["due"]),
                "quantity": _num(d["quantity"]),
                "delivery": _fmt(d["deliverydate"]),
                "delay_days": _days(d["delay"]),
                "status": d["status"] or "",
                "priority": d["priority"],
                "reasons": reasons.get(d["name"], []),
            }
        )
    orders.sort(key=lambda o: -o["delay_days"])

    # Phase names for the routing steps, one query.
    step_op_ids = {s["operation_id"] for s in steps if s["operation_id"]}
    op_desc = dict(
        Operation.objects.using(db)
        .filter(name__in=step_op_ids)
        .values_list("name", "description")
    )

    return JsonResponse(
        {
            "ref": op.reference,
            "type": op.type or "",
            "status": (op.status or "").lower(),
            "tone": tone,
            "item": op.item_id or "",
            "item_description": (op.item.description if op.item_id else "") or "",
            "operation": op.operation_id or "",
            "operation_description": (
                op.operation.description if op.operation_id else ""
            )
            or "",
            "quantity": _num(op.quantity),
            "start": _fmt(op.startdate),
            "end": _fmt(op.enddate),
            "hours": (
                round((op.enddate - op.startdate).total_seconds() / 3600.0, 2)
                if op.startdate and op.enddate
                else 0
            ),
            "delay_days": delay_days,
            "criticality": round(crit, 2),
            "machines": res_by_op.get(op.reference, []),
            "job": {
                "ref": parent.reference,
                "operation": parent.operation_id or "",
                "start": _fmt(parent.startdate),
                "end": _fmt(parent.enddate),
                "quantity": _num(parent.quantity),
                "is_this": parent.reference == op.reference,
                "steps": [
                    {
                        "ref": s["reference"],
                        "operation": s["operation_id"] or "",
                        "description": op_desc.get(s["operation_id"]) or "",
                        "start": _fmt(s["startdate"]),
                        "end": _fmt(s["enddate"]),
                        "status": (s["status"] or "").lower(),
                        "quantity": _num(s["quantity"]),
                        "delay_days": _days(s["delay"]),
                        "machines": [
                            m["name"] for m in res_by_op.get(s["reference"], [])
                        ],
                        "is_this": s["reference"] == op.reference,
                    }
                    for s in steps
                ],
            },
            "consumes": consumes,
            "produces": produces,
            "orders": orders,
            "links": {
                # request.prefix carries the scenario ("/aps1") - without
                # it these links break on any non-default database.
                #
                # 'trace' is the most-delayed order this job feeds (orders
                # is sorted by delay); when a job feeds several, each one
                # carries its own trace link in the orders list above.
                "trace": orders[0]["trace"] if orders else "",
                "order_grid": "%s/data/input/demand/" % prefix,
                "mo": "%s/data/input/manufacturingorder/?reference=%s"
                % (prefix, parent.reference),
                "constraint": "%s/constraint/" % prefix,
                "routing": "%s/data/input/operation/?name=%s"
                % (prefix, parent.operation_id or ""),
            },
        }
    )
