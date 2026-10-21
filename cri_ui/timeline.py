#
# The project timeline.
#
# Two screens that answer the project manager's half of the planning
# question. The machine schedule says what each MACHINE is doing; the
# build graph says what has to happen for one order as a DEPENDENCY
# GRAPH; these say WHEN each piece of one order happens, on a plain date
# axis:
#
#   /projects/           every order as one bar (first op -> last op)
#                        against its due date - the fires first.
#   /projects/<demand>/  one row per operation of that order: the
#                        production runs, their routing steps, the
#                        purchased/transferred parts and the delivery.
#
# One demand IS one project at Sieger - LN splits multi-item customer
# orders before they reach us (handoff/01-domain.md).
#
# Data comes from the engine's own pegging record: every operationplan
# carries plan->'pegging' = {demand: qty}, so one indexed-enough filter
# returns the full set for an order with no graph walk at all. The
# demand FK on operationplan is NOT used - it is only set on delivery
# rows (see freppledb/output/commands.py, ExportPegging).
#
# Rendering deliberately reuses the machine schedule's vocabulary - the
# same .sg-sc-* chart classes, the same %-geometry computed server-side,
# the same schedule.js click-through modal - so the two boards read as
# one product and there is no second layout engine to maintain.
#

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from freppledb.input.models import Demand, OperationPlan

from .buildgraph import KIND, _machines, _pegging, _tone
from .views import _days, _num, _plan_now, _ticks

PAGE_SIZE = 40


# ----------------------------------------------------------------- shared


def _geometry(s, e, start, span):
    """Clamped left/width percentages for one bar inside the window."""
    vs, ve = max(s, start), min(e, start + timedelta(seconds=span))
    if ve < vs:
        ve = vs
    return (
        round(100.0 * (vs - start).total_seconds() / span, 4),
        round(100.0 * (ve - vs).total_seconds() / span, 4),
    )


def _pct(dt, start, span):
    """Position of a single instant, or None when it falls outside."""
    if dt is None:
        return None
    off = (dt - start).total_seconds()
    if off < 0 or off > span:
        return None
    return round(100.0 * off / span, 4)


def _window_for(dates, pad_days=1):
    """A window wrapping every date given, padded a day each side."""
    dates = [d for d in dates if d is not None]
    if not dates:
        return None, None, 0
    start = min(dates).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=pad_days
    )
    end = max(dates).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=pad_days + 1
    )
    days = max(1, int(round((end - start).total_seconds() / 86400.0)))
    return start, end, days


# ------------------------------------------------------------- overview


@login_required(login_url="/data/login/")
def projects(request):
    """
    Every order as one bar against its due date.

    Filtering, sorting and paging run on demand columns only, then ONE
    raw query aggregates the planned span for just the rows on screen.
    A global sort by planned start would need that aggregate over the
    whole table - deliberately not offered.
    """
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    now = _plan_now(db)

    q = (request.GET.get("q") or "").strip()
    show = request.GET.get("show") or "late"
    sort = request.GET.get("sort") or "late"
    seq = (request.GET.get("seq") or "").strip()

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
    if seq:
        qs = qs.filter(category=seq)

    qs = qs.order_by(
        *{
            "late": ("-delay", "due", "name"),
            "due": ("due", "name"),
            "priority": ("priority", "due", "name"),
            "name": ("name",),
        }.get(sort, ("-delay", "due", "name"))
    )

    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    names = [d.name for d in page.object_list]

    # The planned span of each order on this page, in one query over the
    # engine's pegging json. LATERAL unrolls the {demand: qty} keys so a
    # shared production run counts for every order it serves.
    spans = {}
    if names:
        with connections[db].cursor() as c:
            c.execute(
                """
                select k.demand,
                       min(op.startdate)                              as first_start,
                       max(op.enddate)                                as last_end,
                       count(*) filter (where op.type = 'WO')         as steps,
                       count(*) filter (where op.status = 'proposed') as proposed
                from operationplan op
                cross join lateral jsonb_object_keys(
                    coalesce(op.plan->'pegging', '{}'::jsonb)) as k(demand)
                where op.plan->'pegging' ?| %s::text[]
                  and k.demand = any(%s::text[])
                group by k.demand
                """,
                [names, names],
            )
            for demand, first_start, last_end, steps, proposed in c.fetchall():
                spans[demand] = {
                    "first_start": first_start,
                    "last_end": last_end,
                    "steps": steps,
                    "proposed": proposed,
                }

    # A shared axis wrapping everything on the page - spans AND dues, so
    # the due tick of an unstarted order is still on screen.
    dates = []
    for d in page.object_list:
        if d.due:
            dates.append(d.due)
        sp = spans.get(d.name)
        if sp:
            dates.extend((sp["first_start"], sp["last_end"]))
    start, end, days = _window_for(dates)
    span = (end - start).total_seconds() if start else 0

    rows = []
    for d in page.object_list:
        sp = spans.get(d.name)
        delay_days = _days(d.delay)
        row = {
            "name": d.name,
            "item": d.item_id or "",
            "description": (d.item.description if d.item_id else "") or "",
            "customer": d.customer_id or "",
            "quantity": _num(d.quantity),
            "due": d.due,
            "category": d.category or "",
            "delay_days": delay_days,
            "tone": "late" if delay_days > 0.05 else "ok",
            "planned": bool(sp),
            "due_pct": _pct(d.due, start, span) if span else None,
        }
        if sp and span:
            row["left"], row["width"] = _geometry(
                sp["first_start"], sp["last_end"], start, span
            )
            row["first_start"] = sp["first_start"]
            row["last_end"] = sp["last_end"]
            row["steps"] = sp["steps"]
            row["proposed"] = sp["proposed"]
        rows.append(row)

    now_pct = _pct(now, start, span) if span else None

    # Delivery-sequence tags, for the seq filter - hidden while the LN
    # extract leaves category empty everywhere.
    seqs = sorted(
        c
        for c in Demand.objects.using(db)
        .exclude(category__isnull=True)
        .exclude(category="")
        .values_list("category", flat=True)
        .distinct()
    )

    keep = []
    for key, val in (("q", q), ("show", show), ("sort", sort), ("seq", seq)):
        if val and val != "all":
            keep.append("%s=%s" % (key, val))

    return render(
        request,
        "sieger_ui/projects.html",
        context={
            "title": _("projects"),
            "now": now,
            "rows": rows,
            "page": page,
            "q": q,
            "show": show,
            "sort": sort,
            "seq": seq,
            "seqs": seqs,
            "querystring": ("&" + "&".join(keep)) if keep else "",
            "start": start,
            "end": end,
            "ticks": _ticks(start, end, days, db) if start else [],
            "now_pct": now_pct,
            "total": Demand.objects.using(db).count(),
            "filters": (
                ("late", _("late")),
                ("open", _("open")),
                ("all", _("all")),
            ),
            "sorts": (
                ("late", _("most late")),
                ("due", _("due date")),
                ("priority", _("priority")),
                ("name", _("order no.")),
            ),
        },
    )


# ------------------------------------------------------------ one order


@login_required(login_url="/data/login/")
def project_timeline(request, demand):
    """
    Everything the plan does for one order, as rows on a date axis:
    each production run with its routing steps, then the purchased or
    transferred material, then the delivery - against the due date.
    """
    db = getattr(request, "database", DEFAULT_DB_ALIAS) or DEFAULT_DB_ALIAS
    now = _plan_now(db)

    try:
        d = Demand.objects.using(db).select_related("item", "customer").get(name=demand)
    except Demand.DoesNotExist:
        raise Http404("Unknown order")

    ops = list(
        OperationPlan.objects.using(db)
        .filter(plan__pegging__has_key=d.name)
        .values(
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
            "operation_id",
            "operation__priority",
            "plan",
        )
    )
    machines = _machines(db, [o["reference"] for o in ops])

    # -- shape: runs (MO) own their steps (WO, by owner_id); everything
    # else is supply or the delivery itself.
    runs = {o["reference"]: o for o in ops if o["type"] == "MO"}
    steps_by_run = {}
    loose = []
    supply = []
    dlvr = []
    for o in ops:
        if o["type"] == "MO":
            continue
        elif o["type"] == "WO":
            if o["owner_id"] in runs:
                steps_by_run.setdefault(o["owner_id"], []).append(o)
            else:
                loose.append(o)
        elif o["type"] == "DLVR":
            dlvr.append(o)
        else:
            supply.append(o)

    start, end, days = _window_for(
        [o["startdate"] for o in ops]
        + [o["enddate"] for o in ops]
        + ([d.due] if d.due else [])
    )
    span = (end - start).total_seconds() if start else 0

    def row(o, kind_label=None):
        s, e = o["startdate"], o["enddate"]
        if s is None:
            return None
        if e is None or e < s:
            e = s
        delay_days = _days(o["delay"])
        peg = _pegging(o["plan"])
        left, width = _geometry(s, e, start, span)
        return {
            "ref": o["reference"],
            "kind": kind_label or KIND.get(o["type"], o["type"]),
            "label": o["item_id"] or o["operation_id"] or o["reference"],
            "description": o["item__description"] or "",
            "operation": o["operation_id"] or "",
            "status": (o["status"] or "").lower(),
            "qty": _num(o["quantity"]),
            "start": s,
            "end": e,
            "hours": round((e - s).total_seconds() / 3600.0, 2),
            "delay_days": delay_days,
            "tone": _tone(delay_days, o["criticality"]),
            "left": left,
            "width": width,
            "clipped": False,
            "machines": [m["name"] for m in machines.get(o["reference"], [])],
            "peg_qty": _num(peg.get(d.name)) if d.name in peg else "",
            "shared": len(peg) > 1,
        }

    groups = []
    for ref, run in sorted(
        runs.items(), key=lambda kv: (kv[1]["startdate"] or now, kv[0])
    ):
        header = row(run)
        steps = [
            row(o)
            for o in sorted(
                steps_by_run.get(ref, []),
                key=lambda o: (o["operation__priority"] or 0, o["startdate"] or now),
            )
        ]
        groups.append(
            {
                "kind": "run",
                "header": header,
                "rows": [r for r in steps if r],
            }
        )
    if loose:
        groups.append(
            {
                "kind": "steps",
                "header": None,
                "rows": [r for r in (row(o) for o in loose) if r],
            }
        )
    if supply:
        groups.append(
            {
                "kind": "supply",
                "header": None,
                "rows": [
                    r
                    for r in (
                        row(o)
                        for o in sorted(supply, key=lambda o: o["startdate"] or now)
                    )
                    if r
                ],
            }
        )
    if dlvr:
        groups.append(
            {
                "kind": "delivery",
                "header": None,
                "rows": [r for r in (row(o) for o in dlvr) if r],
            }
        )

    delay_days = _days(d.delay)
    return render(
        request,
        "sieger_ui/project_timeline.html",
        context={
            "title": _("project timeline"),
            "now": now,
            "demand": d,
            "item_description": (d.item.description if d.item_id else "") or "",
            "quantity": _num(d.quantity),
            "delay_days": delay_days,
            "tone": "late" if delay_days > 0.05 else "ok",
            "category": d.category or "",
            "groups": groups,
            "op_count": sum(len(g["rows"]) for g in groups)
            + sum(1 for g in groups if g["header"]),
            "start": start,
            "end": end,
            "ticks": _ticks(start, end, days, db) if start else [],
            "now_pct": _pct(now, start, span) if span else None,
            "due_pct": _pct(d.due, start, span) if span else None,
        },
    )
