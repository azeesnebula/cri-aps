#
# The build graph - a new sub-application, additive to the Sieger UI.
#
# WHAT IT SHOWS
# -------------
# One finished machine, sitting on its delivery date, and every operation
# that has to happen underneath it to produce that machine - laid out
# downwards on a real date axis, so scrolling down travels backwards in
# time toward the earliest manufacturing activity.
#
# WHERE THE GRAPH COMES FROM (nothing here is invented)
# ----------------------------------------------------
# The planning engine already writes the dependency graph it solved into
# operationplan.plan, a jsonb column populated for 100% of the 56,548
# operationplans in this database:
#
#   plan->'upstream_opplans'    [[reference, quantity, ...], ...]
#   plan->'downstream_opplans'  [[reference, quantity, ...], ...]
#   plan->'pegging'             {demand name: quantity, ...}
#
# Predecessors, successors, and the per-order allocation of a shared
# production run are therefore FACTS from the solver. This module walks
# them. It does not re-derive pegging by guessing at material flows, and it
# never fabricates a link the engine did not record.
#
# ONE STRUCTURAL QUIRK, HANDLED EXPLICITLY
# ----------------------------------------
# A routing explodes into one MO parent plus one WO child per step, and the
# engine links the MO to its OWN first step in both directions:
#
#   MO 28278   upstream [28282]   downstream [28282]
#   WO 28282   upstream [28278]   downstream [28290]
#
# That is a container relationship, not a dependency, and drawing it as an
# edge would put a two-node cycle in every routing. _collect() classifies
# any edge between a node and its own owner as containment instead: the MO
# becomes the production RUN that groups its steps. What is left is the
# true sequence - 10-CUTG -> 20-TURN -> ... -> 100-TMAS -> delivery.
#
# Traversal still passes THROUGH the MO, because that is where component
# supply attaches: loader/load_bom.py writes operationmaterial against the
# routing header, so a manufacturing order's upstream links are its
# components' production runs.
#
# DEPTH IS NOT HARD-CODED
# -----------------------
# Depth is whatever the data supports. Today the deepest routings run ten
# steps, so the deepest graphs are twelve levels (order, delivery, ten
# operations). operationmaterial is currently empty - no bill of materials
# has been loaded - so a manufacturing order consumes nothing and each
# graph is one production run deep. The moment loader/load_bom.py is run,
# the component runs appear in these same upstream links and this code
# renders them at whatever depth they reach, with no change here.
#
# LAYOUT IS DELIBERATELY NOT DONE HERE
# ------------------------------------
# The client computes x/y, so expanding or collapsing a branch re-lays-out
# instantly with no round trip. This module supplies nodes, edges and real
# dates only.
#

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from freppledb.input.models import (
    Demand,
    Operation,
    OperationPlan,
    OperationPlanResource,
)

from .views import _days, _fmt, _num, _plan_now

# Bounds. A graph is a popup-sized object, not a report; these stop a
# pathological bill of materials from turning one click into a huge
# payload. Both are reported to the client when they bite.
MAX_NODES = 4000
MAX_DEPTH = 60

PAGE_SIZE = 48

# frePPLe operationplan types, in the language of the shop floor.
KIND = {
    "MO": "run",  # manufacturing order - a production run
    "WO": "operation",  # one routing step of that run
    "DLVR": "delivery",  # shipment against the customer order
    "PO": "purchase",  # bought part
    "DO": "transfer",  # moved between locations
    "STCK": "stock",  # taken from stock on hand
}


# ----------------------------------------------------------------- helpers


def _iso(dt):
    return dt.isoformat() if dt else None


def _links(plan, key):
    """
    The reference side of plan->upstream_opplans / downstream_opplans.

    Entries are [reference, quantity, ...]. frePPLe has widened that tuple
    before, so read positionally and tolerate anything past the second slot.
    """
    out = []
    for entry in (plan or {}).get(key) or []:
        if isinstance(entry, (list, tuple)) and entry:
            qty = None
            if len(entry) > 1:
                try:
                    qty = float(entry[1])
                except (TypeError, ValueError):
                    qty = None
            out.append((str(entry[0]), qty))
    return out


def _pegging(plan):
    """plan->pegging as {demand: qty}, tolerating a malformed entry."""
    out = {}
    for name, qty in ((plan or {}).get("pegging") or {}).items():
        try:
            out[str(name)] = float(qty)
        except (TypeError, ValueError):
            continue
    return out


def _tone(delay_days, criticality):
    if float(criticality or 0) >= 999:
        return "crit"
    return "late" if delay_days > 0.05 else "ok"


# --------------------------------------------------------- product picker


@login_required(login_url="/data/login/")
def product_list(request):
    """
    The machines currently being planned, as cards.

    Every field on a card is a column of the demand table. The single
    derived number is 'steps' - the length of the item's routing - which
    also decides whether an order can be built at all: an item with no
    routing is unschedulable, and saying so here saves the planner a click
    into an empty graph.
    """
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    now = _plan_now(db)

    # The shipment picker that used to live here was replaced by the
    # build map (shipmap.py) on 9 Oct 2026. This view is now only the
    # per-order picker, reached from the map's "single order" link.
    # It used to decide between the two on ?view=orders, which meant
    # /build-graph/orders/ - no query string - still rendered the old
    # shipment screen.
    q = (request.GET.get("q") or "").strip()
    show = request.GET.get("show") or "all"
    sort = request.GET.get("sort") or "due"

    qs = Demand.objects.using(db).select_related("item")
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(item__name__icontains=q)
            | Q(item__description__icontains=q)
            | Q(customer__name__icontains=q)
        )
    if show == "open":
        qs = qs.filter(status="open")
    elif show == "late":
        qs = qs.filter(delay__gt="0 seconds")

    # An item is buildable when it has a routing. Same test the cockpit
    # uses for its "blocked orders" step.
    routed = set(
        Operation.objects.using(db)
        .filter(type="routing", item__isnull=False)
        .values_list("item_id", flat=True)
    )
    if show == "blocked":
        qs = qs.exclude(item_id__in=routed)
    elif show == "buildable":
        qs = qs.filter(item_id__in=routed)

    qs = qs.order_by(
        *{
            "due": ("due", "name"),
            "late": ("-delay", "due"),
            "priority": ("priority", "due"),
            "qty": ("-quantity", "due"),
            "name": ("name",),
        }.get(sort, ("due", "name"))
    )

    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))

    # One extra query, for the 48 rows actually on screen: how many steps
    # the item's routing has. operation.owner links a step to its routing
    # header and operation.priority carries the step number, so this is the
    # routing's real length rather than a count of planned work orders.
    items = {d.item_id for d in page.object_list if d.item_id}
    steps = {}
    if items:
        for row in (
            Operation.objects.using(db)
            .filter(owner__type="routing", owner__item_id__in=items)
            .values("owner__item_id")
            .annotate(n=Count("name"))
        ):
            steps[row["owner__item_id"]] = row["n"]

    cards = []
    for d in page.object_list:
        delay_days = _days(d.delay)
        cards.append(
            {
                "name": d.name,
                "item": d.item_id or "",
                "description": (d.item.description if d.item_id else "") or "",
                "customer": d.customer_id or "",
                "quantity": _num(d.quantity),
                "due": d.due,
                "delivery": d.deliverydate,
                "delay_days": delay_days,
                "status": (d.status or "").lower(),
                "priority": d.priority,
                "buildable": d.item_id in routed,
                "steps": steps.get(d.item_id, 0),
                "tone": "late" if delay_days > 0.05 else "ok",
            }
        )

    # Keep the current filter/sort on the pager links.
    keep = []
    for key, val in (("q", q), ("show", show), ("sort", sort)):
        if val and val != "all":
            keep.append("%s=%s" % (key, val))

    return render(
        request,
        "sieger_ui/build_select.html",
        context={
            "title": _("build graph"),
            "now": now,
            "cards": cards,
            "page": page,
            "q": q,
            "show": show,
            "sort": sort,
            "querystring": ("&" + "&".join(keep)) if keep else "",
            "total": Demand.objects.using(db).count(),
            "filters": (
                ("all", _("all")),
                ("open", _("open")),
                ("late", _("late")),
                ("buildable", _("buildable")),
                ("blocked", _("no routing")),
            ),
            "sorts": (
                ("due", _("due date")),
                ("late", _("most late")),
                ("priority", _("priority")),
                ("qty", _("quantity")),
                ("name", _("order no.")),
            ),
        },
    )


# ------------------------------------------------------------ the traversal

_OP_FIELDS = (
    "reference",
    "owner_id",
    "type",
    "status",
    "quantity",
    "startdate",
    "enddate",
    "delay",
    "criticality",
    "item_id",
    "item__description",
    "location_id",
    "operation_id",
    "operation__description",
    "operation__priority",
    "demand_id",
    "plan",
)


def _fetch(db, refs):
    return list(
        OperationPlan.objects.using(db)
        .filter(reference__in=list(refs))
        .values(*_OP_FIELDS)
    )


def _collect(db, seeds):
    """
    Breadth-first upstream walk from the delivery operationplans.

    Returns (rows_by_ref, edges, contains, truncated, hit_depth).

      edges     set of (upstream_ref, downstream_ref) - a real dependency
      contains  {mo_ref: {step_refs}} - routing containment, not a dependency

    The visited set is what makes this terminate: the MO/first-step pair
    links both ways, and several steps can share one predecessor.
    """
    seen = {}
    edges = set()
    contains = {}
    truncated = False
    depth = 0

    frontier = [r for r in _fetch(db, seeds)]
    for r in frontier:
        seen[r["reference"]] = r

    while frontier and depth < MAX_DEPTH:
        wanted = set()
        for r in frontier:
            for up, _qty in _links(r["plan"], "upstream_opplans"):
                if up == r["reference"]:
                    continue
                edges.add((up, r["reference"]))
                if up not in seen:
                    wanted.add(up)
        if not wanted:
            break
        if len(seen) + len(wanted) > MAX_NODES:
            truncated = True
            wanted = set(list(wanted)[: max(0, MAX_NODES - len(seen))])
            if not wanted:
                break
        frontier = _fetch(db, wanted)
        for r in frontier:
            seen[r["reference"]] = r
        depth += 1
    else:
        if frontier:
            truncated = True

    # Split containment out of the edge set. An MO and its own routing step
    # are linked in both directions by the engine; that pair is a container
    # relationship and must not be drawn as a dependency.
    real = set()
    for a, b in edges:
        ra, rb = seen.get(a), seen.get(b)
        if ra is None or rb is None:
            continue
        if ra.get("owner_id") == b:
            contains.setdefault(b, set()).add(a)
            continue
        if rb.get("owner_id") == a:
            contains.setdefault(a, set()).add(b)
            continue
        real.add((a, b))

    # Steps reached without ever traversing the container edge still belong
    # to their run - take containment straight off owner_id.
    for ref, r in seen.items():
        owner = r.get("owner_id")
        if owner and owner in seen:
            contains.setdefault(owner, set()).add(ref)

    return seen, real, contains, truncated, depth


def _machines(db, refs):
    out = {}
    for r in (
        OperationPlanResource.objects.using(db)
        .filter(operationplan_id__in=list(refs))
        .values("operationplan_id", "resource_id", "setup")
        .order_by("resource_id")
    ):
        out.setdefault(r["operationplan_id"], []).append(
            {"name": r["resource_id"], "setup": r["setup"] or ""}
        )
    return out


def _node(r, machines, peg_index):
    """One operationplan as a graph node. Every field is a real column."""
    ref = r["reference"]
    delay_days = _days(r["delay"])
    kind = KIND.get(r["type"] or "", "operation")
    pegging = _pegging(r["plan"])

    start, end = r["startdate"], r["enddate"]
    hours = (
        round((end - start).total_seconds() / 3600.0, 2)
        if start and end and end >= start
        else 0.0
    )

    return {
        "id": ref,
        "ref": ref,
        "kind": kind,
        "type": r["type"] or "",
        "item": r["item_id"] or "",
        "description": r["item__description"] or "",
        "location": r["location_id"] or "",
        "operation": r["operation_id"] or "",
        "operation_description": r["operation__description"] or "",
        # operation.priority is the routing step number: 10, 20, 30...
        "seq": r["operation__priority"],
        "qty": _num(r["quantity"]),
        "qty_raw": float(r["quantity"] or 0),
        "start": _iso(start),
        "end": _iso(end),
        "start_txt": _fmt(start),
        "end_txt": _fmt(end),
        "hours": hours,
        "status": (r["status"] or "").lower(),
        "delay_days": delay_days,
        "criticality": round(float(r["criticality"] or 0), 2),
        "tone": _tone(delay_days, r["criticality"]),
        "machines": [m["name"] for m in machines.get(ref, [])],
        "setup": next((m["setup"] for m in machines.get(ref, []) if m["setup"]), ""),
        "run": r["owner_id"] or None,
        "demand": r["demand_id"] or "",
        # How many customer orders this operationplan is pegged to. >1 is
        # the shared-production case, and the number is the solver's own.
        "npeg": len(pegging),
        "shared": len(pegging) > 1,
        "peg_qty": round(peg_index.get(ref, 0.0), 3),
    }


def _graph(db, demand):
    """The whole build graph for one customer order."""
    seeds = list(
        OperationPlan.objects.using(db)
        .filter(demand_id=demand.name)
        .values_list("reference", flat=True)
    )
    if not seeds:
        return None

    seen, edges, contains, truncated, depth = _collect(db, seeds)
    machines = _machines(db, seen.keys())

    # How much of each operationplan is for THIS order. Straight out of the
    # solver's pegging - not apportioned by us.
    peg_index = {
        ref: _pegging(r["plan"]).get(demand.name, 0.0) for ref, r in seen.items()
    }

    nodes = {ref: _node(r, machines, peg_index) for ref, r in seen.items()}

    # Runs, from the containment map. A run is one manufacturing order: the
    # thing a planner releases, and the unit of expand/collapse.
    runs = []
    for mo_ref, members in contains.items():
        r = seen.get(mo_ref)
        if r is None:
            continue
        pegging = _pegging(r["plan"])
        allocated = sum(pegging.values())
        total = float(r["quantity"] or 0)
        steps = sorted(
            (nodes[m] for m in members if m in nodes),
            key=lambda n: (n["seq"] is None, n["seq"], n["start"] or ""),
        )
        runs.append(
            {
                "id": mo_ref,
                "item": r["item_id"] or "",
                "description": r["item__description"] or "",
                "operation": r["operation_id"] or "",
                "qty": _num(r["quantity"]),
                "qty_raw": total,
                "start": _iso(r["startdate"]),
                "end": _iso(r["enddate"]),
                "status": (r["status"] or "").lower(),
                "tone": _tone(_days(r["delay"]), r["criticality"]),
                "delay_days": _days(r["delay"]),
                "steps": [n["id"] for n in steps],
                "shared": len(pegging) > 1,
                "orders": len(pegging),
                "for_this_order": round(pegging.get(demand.name, 0.0), 3),
                "allocated": round(allocated, 3),
                "remaining": round(total - allocated, 3),
            }
        )
    runs.sort(key=lambda x: (x["start"] or "", x["id"]))

    # Deliveries carry no operation of their own; label them for what they
    # are so the top of the graph reads as a shipment, not a blank step.
    for n in nodes.values():
        if n["kind"] == "delivery" and not n["operation"]:
            n["operation_description"] = "ship to customer"

    dates = [n["start"] for n in nodes.values() if n["start"]] + [
        n["end"] for n in nodes.values() if n["end"]
    ]

    return {
        "order": {
            "name": demand.name,
            "item": demand.item_id or "",
            "description": (demand.item.description if demand.item_id else "") or "",
            "customer": demand.customer_id or "",
            "quantity": _num(demand.quantity),
            "due": _iso(demand.due),
            "due_txt": _fmt(demand.due),
            "delivery": _iso(demand.deliverydate),
            "delivery_txt": _fmt(demand.deliverydate),
            "delay_days": _days(demand.delay),
            "status": (demand.status or "").lower(),
            "priority": demand.priority,
            "location": demand.location_id or "",
            "tone": "late" if _days(demand.delay) > 0.05 else "ok",
        },
        "nodes": list(nodes.values()),
        "runs": runs,
        "edges": sorted(edges),
        "seeds": seeds,
        "meta": {
            "nodes": len(nodes),
            "runs": len(runs),
            "edges": len(edges),
            "depth": depth,
            "truncated": truncated,
            "max_nodes": MAX_NODES,
            "min_date": min(dates) if dates else None,
            "max_date": max(dates) if dates else None,
        },
    }


# --------------------------------------------------------------- the views


def _demand_or_404(db, name):
    try:
        return Demand.objects.using(db).select_related("item").get(name=name)
    except Demand.DoesNotExist:
        raise Http404("no such order")


@login_required(login_url="/data/login/")
def build_graph(request, demand):
    """The graph page itself - a shell; the graph arrives as JSON."""
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    d = _demand_or_404(db, demand)

    # A short list of neighbouring orders, so a planner can flip between
    # machines without going back to the picker (requirement: switch
    # quickly between Machine A | B | C | D).
    siblings = list(
        Demand.objects.using(db)
        .filter(due__isnull=False)
        .exclude(name=d.name)
        .order_by("due", "name")
        .values("name", "item_id", "due")[:12]
    )

    return render(
        request,
        "sieger_ui/build_graph.html",
        context={
            "title": _("build graph"),
            "now": _plan_now(db),
            "demand": d,
            "siblings": siblings,
        },
    )


@login_required(login_url="/data/login/")
def build_graph_data(request, demand):
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    d = _demand_or_404(db, demand)
    graph = _graph(db, d)
    if graph is None:
        # No operationplan references this order at all. That is a real
        # planning outcome - usually an item with no routing - so report it
        # rather than inventing a placeholder chain.
        return JsonResponse(
            {
                "order": {
                    "name": d.name,
                    "item": d.item_id or "",
                    "description": (d.item.description if d.item_id else "") or "",
                    "customer": d.customer_id or "",
                    "quantity": _num(d.quantity),
                    "due": _iso(d.due),
                    "due_txt": _fmt(d.due),
                    "delivery": _iso(d.deliverydate),
                    "delivery_txt": _fmt(d.deliverydate),
                    "delay_days": _days(d.delay),
                    "status": (d.status or "").lower(),
                    "priority": d.priority,
                    "tone": "late" if _days(d.delay) > 0.05 else "ok",
                },
                "nodes": [],
                "runs": [],
                "edges": [],
                "meta": {"nodes": 0, "runs": 0, "edges": 0, "empty": True},
            }
        )
    return JsonResponse(graph)


# The all-at-once SHIPMENT graph that lived here was removed on
# 9 Oct 2026. It drew every production run of a shipment in one go -
# up to 2,148 cards and 2,400 wires - which was unreadable and slow.
# shipmap.py replaced it with a map that loads a level at a time.
# The per-ORDER graph below is unchanged and still in use.

@login_required(login_url="/data/login/")
def run_detail(request, ref):
    """
    A production run, and who it is being produced for.

    The allocation table is plan->pegging verbatim: the solver's own split
    of this run across the customer orders that consume it. Where the sum
    is less than the run quantity, the balance is genuinely uncommitted -
    it is not padded to make the numbers meet.
    """
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    try:
        op = (
            OperationPlan.objects.using(db)
            .select_related("item", "operation")
            .get(reference=ref)
        )
    except OperationPlan.DoesNotExist:
        raise Http404("no such operationplan")

    pegging = _pegging(op.plan)
    total = float(op.quantity or 0)
    allocated = sum(pegging.values())

    orders = []
    if pegging:
        by_name = {
            d["name"]: d
            for d in Demand.objects.using(db)
            .filter(name__in=list(pegging))
            .values("name", "item_id", "customer_id", "due", "deliverydate", "delay",
                    "quantity", "status", "priority")
        }
        for name, qty in pegging.items():
            d = by_name.get(name, {})
            orders.append(
                {
                    "demand": name,
                    "quantity": _num(qty),
                    "share": round(100.0 * qty / total, 1) if total else 0.0,
                    "item": d.get("item_id") or "",
                    "customer": d.get("customer_id") or "",
                    "due": _fmt(d.get("due")),
                    "delivery": _fmt(d.get("deliverydate")),
                    "delay_days": _days(d.get("delay")),
                    "order_qty": _num(d.get("quantity")),
                    "status": (d.get("status") or "").lower(),
                    "priority": d.get("priority"),
                }
            )
        orders.sort(key=lambda o: -float(o["quantity"] or 0))

    steps = []
    machines = _machines(db, [op.reference])
    for s in (
        OperationPlan.objects.using(db)
        .filter(owner_id=op.reference)
        .select_related("operation")
        .values(
            "reference",
            "operation_id",
            "operation__description",
            "operation__priority",
            "startdate",
            "enddate",
            "status",
            "quantity",
            "delay",
        )
    ):
        steps.append(
            {
                "ref": s["reference"],
                "seq": s["operation__priority"],
                "operation": s["operation_id"] or "",
                "description": s["operation__description"] or "",
                "start": _fmt(s["startdate"]),
                "end": _fmt(s["enddate"]),
                "status": (s["status"] or "").lower(),
                "quantity": _num(s["quantity"]),
                "delay_days": _days(s["delay"]),
            }
        )
    steps.sort(key=lambda s: (s["seq"] is None, s["seq"], s["start"]))
    if steps:
        step_machines = _machines(db, [s["ref"] for s in steps])
        for s in steps:
            s["machines"] = [m["name"] for m in step_machines.get(s["ref"], [])]

    return JsonResponse(
        {
            "ref": op.reference,
            "type": op.type or "",
            "kind": KIND.get(op.type or "", "operation"),
            "item": op.item_id or "",
            "description": (op.item.description if op.item_id else "") or "",
            "operation": op.operation_id or "",
            "location": op.location_id or "",
            "quantity": _num(op.quantity),
            "start": _fmt(op.startdate),
            "end": _fmt(op.enddate),
            "status": (op.status or "").lower(),
            "delay_days": _days(op.delay),
            "criticality": round(float(op.criticality or 0), 2),
            "machines": [m["name"] for m in machines.get(op.reference, [])],
            "shared": len(pegging) > 1,
            "orders": orders,
            "order_count": len(pegging),
            "allocated": _num(allocated),
            "remaining": _num(round(total - allocated, 6)),
            "steps": steps,
            "links": {
                "mo": "/data/input/manufacturingorder/?reference=%s" % op.reference,
                "routing": "/data/input/operation/?name=%s" % (op.operation_id or ""),
                "op": "/machine-schedule/op/%s/" % op.reference,
            },
        }
    )
