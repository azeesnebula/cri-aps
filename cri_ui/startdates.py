"""
Start dates - the latest each shipment can start, and why.

    GET /start-dates/      one row per shipment: the plan's own dates, the
                           arithmetic floor, and a Measure button

WHAT THIS SCREEN IS, AND DELIBERATELY IS NOT
--------------------------------------------
Every number in the body of a row is ARITHMETIC: this shipment's own
routing and bill of materials, walked and summed, nothing else. It answers
"what is the least time this can physically take, as modelled?" and
therefore "when must it start to meet its date?". It says NOTHING about
contention - whether the machines are free when this shipment needs them
is a question only a full solve can answer, and the Measure button runs
exactly that through the What-to-do-next machinery (improve.prove), on a
copy, never on the live plan.

There is no predicted date anywhere here. A predicted "start here, arrive
there" was built once and was wrong by a factor of five - the note above
improve._suggest_idle_twin records why, and the bar any estimator must
clear. This screen stays on the honest side of that line: the floor is
exact, the plan dates are the engine's own, and everything in between is
measured or absent.

THE TWO KINDS OF DATES, NEVER BLURRED
-------------------------------------
  plan dates    (header of each row) - deliverydate/delay from the LAST
                SOLVE. They move every replan, so they are stamped with
                the run they came from.
  floor dates   (body of each row) - arithmetic over master data. They
                move only when the routing, the BOM, a lead time or the
                calendar changes. Acting on shipment A never moves
                shipment B's floor - a floor cannot cascade.

HOW THE FLOOR IS SUMMED
-----------------------
For a made item, elapsed time from "go" to "done", this shipment alone on
an empty plant:

    floor(item, qty) = buying pole                    longest branch of the
                                                      BOM below it, recursed
                     + machining, through the calendar:
                         (setup + cycle x qty) / (hours/day x efficiency),
                         stretched over non-working days
                     + transfer time between steps    posttime, as loaded

For a bought item it is simply the supplier lead time. Material is
consumed at the START of a routing (the loader writes type='start'), so a
parent cannot begin until its longest child has arrived - the pole and the
parent's own work are SEQUENTIAL, which is why they add.

A shipment is a set of orders sharing a delivery-sequence tag (untagged
orders each stand alone - the same convention improve.py measures by, so
the Measure button and the floor always talk about the same thing). It
goes when its LAST order is done, so the shipment floor is the max over
its members.

WHAT THE FLOOR INHERITS, STATED ON SCREEN
-----------------------------------------
The floor is only as real as the model. Today that means: transfer time is
a 24h-per-hop placeholder (and soft - the solver may compress it, so the
true floor is if anything LOWER); thousands of steps carry no cycle time
(floor optimistic); quantities and due dates are generated. Each row says
which of these touch it, because a clean-looking date with a dirty
pedigree is how a screen starts lying.
"""

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from freppledb.execute.models import Task

from .improve import (
    P_MEASURED,
    _cal_headroom,
    _facts,
    _fingerprint,
    _json_param,
    _measured_sentence,
)
from .reports import _db, _rows
from .views import _plan_now, _ship_names

# Recursion guards. Sieger's BOM is ~6 levels deep; 30 tolerates any sane
# extract while still stopping a cycle that slipped past the loader.
MAX_DEPTH = 30

# When one branch of a BOM needs more than this many components PER UNIT
# ORDERED, the quantities are not believable as per-unit figures and the
# floor built on them is not usable. This screen found real chains of
# 240 x 190 x 190 per unit - twelve ordered units exploding to 104 million
# parts and 1.7 million machining hours - which is load_bom.py's recorded
# Assumption A ("BOM Unit is per-unit") being wrong for those rows. Such a
# shipment is FLAGGED, never silently ranked: a 425-year floor at the top
# of the urgency list would discredit every honest row below it.
QTY_SUSPECT_PER_UNIT = 10_000


def _shift(when, days):
    """
    when + days, or None if that lands outside the dates Python can hold.

    A floor summed from unbelievable BOM quantities can reach tens of
    thousands of years, and datetime arithmetic on it raises OverflowError
    rather than returning something absurd. Those rows are flagged as
    unusable anyway, so the date is simply absent - but the exception has
    to be caught HERE, or one bad shipment takes the whole screen down and
    every honest row with it.
    """
    if when is None:
        return None
    try:
        return when + timedelta(days=days)
    except (OverflowError, OSError, ValueError):
        return None

PAGE_SIZE = 40


# ======================================================================
# Master data, read once per request into plain dicts
# ======================================================================

ROUTING_SQL = """
select p.item_id as item, p.name as routing,
       count(c.name) as steps,
       count(*) filter (
         where coalesce(extract(epoch from c.duration), 0) = 0
           and coalesce(extract(epoch from c.duration_per), 0) = 0
       ) as zero_steps,
       coalesce(sum(extract(epoch from c.duration)), 0) as setup_s,
       coalesce(sum(extract(epoch from c.duration_per)), 0) as per_s,
       coalesce(sum(extract(epoch from c.posttime)), 0) as transfer_s
from operation p
join operation c on c.owner_id = p.name
where p.type = 'routing' and p.item_id is not null
group by 1, 2
"""

# Consume rows only. The loader attaches them to the routing PARENT, but a
# future extract may hang them on a step - owner_id resolves either to the
# routing whose floor they belong to.
BOM_SQL = """
select coalesce(p.name, o.name) as routing,
       m.item_id as child,
       abs(m.quantity) as q
from operationmaterial m
join operation o on o.name = m.operation_id
left join operation p on p.name = o.owner_id and p.type = 'routing'
where m.quantity < 0
"""

BUY_SQL = """
select item_id, min(extract(epoch from leadtime)) / 86400.0 as days
from itemsupplier
group by 1
"""

DEMAND_SQL = """
select name, item_id, coalesce(quantity, 1) as qty, due,
       coalesce(nullif(category, ''), name) as tag,
       deliverydate,
       extract(epoch from delay) / 86400.0 as delay_d
from demand
where status in ('open', 'quote')
"""

# What the CURRENT plan actually did per shipment: the date its cutting
# really began - the earliest routing step the engine pegged to any of
# its orders. This is the measured counterpart of the arithmetic floor.
PLAN_CUT_SQL = """
select coalesce(nullif(d.category, ''), d.name) as tag,
       min(op.startdate) as first_cut
from operationplan op
cross join lateral jsonb_object_keys(
    coalesce(op.plan->'pegging', '{}'::jsonb)) pegged(k)
join demand d on d.name = pegged.k
where op.type = 'WO'
group by 1
"""

# The plant's own working pattern, straight from the location's calendar.
# No constant: a third shift, a shutdown week or a real per-site calendar
# lands here with no code change - the same rule the plan board's weekend
# tint and /bottlenecks/ already follow.
PLANT_CAL_SQL = """
select to_char(b.starttime, 'HH24:MI:SS') as s,
       to_char(b.endtime, 'HH24:MI:SS') as e,
       b.sunday, b.monday, b.tuesday, b.wednesday,
       b.thursday, b.friday, b.saturday
from location l
join calendarbucket b on b.calendar_id = l.available_id
where b.value > 0
order by b.starttime
"""

EFFICIENCY_SQL = """
select coalesce(avg(efficiency), 100) as eff
from resource
where type = 'default' and coalesce(efficiency, 0) > 0
"""


def _plant_pattern(db):
    """(working hours per day, working days per week, efficiency %)."""
    shifts = []
    for r in _rows(db, PLANT_CAL_SQL):
        flags = [r["sunday"], r["monday"], r["tuesday"], r["wednesday"],
                 r["thursday"], r["friday"], r["saturday"]]
        wd = {6 if i == 0 else i - 1 for i, on in enumerate(flags) if on}
        shifts.append((r["s"] or "00:00:00", r["e"] or "23:59:59", wd))
    hours_per_day, days_per_week = _cal_headroom(shifts)
    eff = _rows(db, EFFICIENCY_SQL)
    return hours_per_day, days_per_week, float(eff[0]["eff"]) if eff else 100.0


class _Floor:
    """
    The arithmetic floor per (item, quantity), with the master data held
    in dicts so ten thousand demands cost one read of each table.
    """

    def __init__(self, db):
        self.routing = {}       # item -> routing aggregates
        for r in _rows(db, ROUTING_SQL):
            self.routing[r["item"]] = r
        self.by_routing = {r["routing"]: r for r in self.routing.values()}

        self.bom = {}           # routing name -> [(child item, qty per unit)]
        for r in _rows(db, BOM_SQL):
            self.bom.setdefault(r["routing"], []).append((r["child"], r["q"]))

        self.buy = {r["item_id"]: r["days"] for r in _rows(db, BUY_SQL)}

        hours, wdays, eff = _plant_pattern(db)
        # One effective working hour costs this many calendar hours: the
        # day is only partly worked, the week only partly open, and the
        # machines run below nominal.
        self.hours_per_day = hours
        self.days_per_week = wdays
        self.efficiency = eff
        self._memo = {}

    def _elapsed_days(self, work_hours):
        """Machining hours -> calendar days, through the plant pattern."""
        if work_hours <= 0:
            return 0.0
        eff = self.efficiency / 100.0 or 1.0
        working_days = work_hours / (self.hours_per_day * eff)
        return working_days * 7.0 / (self.days_per_week or 7)

    def compute(self, item, qty, _depth=0, _stack=None):
        """
        Returns a dict:
          days        total floor, calendar days
          buy_days    the buying pole below this item
          mach_days   this item's own machining, elapsed
          mach_hours  the same in working hours
          transfer_days
          pole        name of the child carrying the pole ('' if none)
          peak_qty    largest quantity any node below is asked to make -
                      the BOM-explosion sanity signal
          zero_steps / steps   cycle-time coverage, this level only
          kind        'make' | 'buy' | 'none'
        """
        qty = float(qty or 1)
        key = (item, round(qty, 3))
        hit = self._memo.get(key)
        if hit is not None:
            return hit

        r = self.routing.get(item)
        if r is None:
            # No routing: bought if a supplier exists, a dead end if not.
            days = self.buy.get(item)
            out = {
                "days": float(days or 0.0),
                "buy_days": float(days or 0.0),
                "mach_days": 0.0, "mach_hours": 0.0, "transfer_days": 0.0,
                "pole": "", "zero_steps": 0, "steps": 0, "peak_qty": qty,
                "kind": "buy" if days is not None else "none",
            }
            self._memo[key] = out
            return out

        # Cycle and depth guards: a poisoned BOM must degrade to "this
        # branch contributes nothing", never to a crash or a hang.
        if _depth >= MAX_DEPTH or (_stack and item in _stack):
            out = {
                "days": 0.0, "buy_days": 0.0, "mach_days": 0.0,
                "mach_hours": 0.0, "transfer_days": 0.0, "pole": "",
                "zero_steps": 0, "steps": 0, "peak_qty": qty, "kind": "make",
            }
            self._memo[key] = out
            return out

        stack = (_stack or frozenset()) | {item}
        pole_days, pole_name, peak = 0.0, "", qty
        for child, per_unit in self.bom.get(r["routing"], ()):
            sub = self.compute(child, qty * float(per_unit or 1),
                               _depth + 1, stack)
            peak = max(peak, sub["peak_qty"])
            if sub["days"] > pole_days:
                pole_days, pole_name = sub["days"], child

        mach_hours = (r["setup_s"] + r["per_s"] * qty) / 3600.0
        mach_days = self._elapsed_days(mach_hours)
        transfer_days = r["transfer_s"] / 86400.0

        out = {
            "days": pole_days + mach_days + transfer_days,
            "buy_days": pole_days,
            "mach_days": mach_days,
            "mach_hours": mach_hours,
            "transfer_days": transfer_days,
            "pole": pole_name,
            "zero_steps": int(r["zero_steps"]),
            "steps": int(r["steps"]),
            "peak_qty": peak,
            "kind": "make",
        }
        self._memo[key] = out
        return out


# ======================================================================
# The screen
# ======================================================================

def _last_run(db):
    return (
        Task.objects.using(db)
        .filter(name="runplan")
        .order_by("-id")
        .values("id", "finished", "status")
        .first()
    )


@login_required(login_url="/data/login/")
def start_dates(request):
    db = _db(request)
    now = _plan_now(db)
    floor = _Floor(db)

    # ---- fold orders into shipments -----------------------------------
    ships = {}
    for d in _rows(db, DEMAND_SQL):
        s = ships.setdefault(d["tag"], {
            "tag": d["tag"], "orders": 0, "unplanned": 0,
            "due": None, "finish": None, "delay_d": None,
            "floor": None, "floor_member": "", "floor_qty": 1.0,
        })
        s["orders"] += 1
        if d["due"] and (s["due"] is None or d["due"] < s["due"]):
            s["due"] = d["due"]
        if d["deliverydate"] is None:
            s["unplanned"] += 1
        elif s["finish"] is None or d["deliverydate"] > s["finish"]:
            s["finish"] = d["deliverydate"]
        if d["delay_d"] is not None and (
                s["delay_d"] is None or d["delay_d"] > s["delay_d"]):
            s["delay_d"] = d["delay_d"]

        # The shipment goes when its LAST order is done, so its floor is
        # the slowest member's floor.
        f = floor.compute(d["item_id"], d["qty"])
        if s["floor"] is None or f["days"] > s["floor"]["days"]:
            s["floor"] = f
            s["floor_member"] = d["name"]
            s["floor_qty"] = float(d["qty"] or 1)

    # Stored measurements, shown only against the plan they were taken on.
    # _facts is a heavy aggregate, so it only runs when there is anything
    # stored at all - which by default there is not.
    measured = _json_param(db, P_MEASURED, {})
    fp = _fingerprint(db, _facts(db)) if isinstance(measured, dict) and measured else None

    rows = []
    impossible = late = overdue_start = no_route = suspect = 0
    plan_cut = {r["tag"]: r["first_cut"] for r in _rows(db, PLAN_CUT_SQL)}
    ship_names = _ship_names(db)
    for s in ships.values():
        f = s["floor"] or {"days": 0, "kind": "none", "peak_qty": 0}
        due = s["due"]
        # One ORDERED UNIT of this shipment explodes to how many parts
        # down its worst branch? Past the bound, the BOM quantities are
        # not believable as per-unit figures and neither is the floor.
        # A ratio, not an absolute: a big order with a sane BOM must
        # never trip it.
        explode = f.get("peak_qty", 0) / (s["floor_qty"] or 1)
        qty_suspect = f["kind"] == "make" and explode > QTY_SUSPECT_PER_UNIT
        lead = f["days"]
        shop_lead = f.get("mach_days", 0) + f.get("transfer_days", 0)
        # A floor built on unbelievable BOM quantities can run to tens of
        # thousands of years, and date arithmetic on it raises
        # OverflowError - which took the whole page down, every row with
        # it, over a handful of rows this screen was already going to
        # flag as unusable. The row still appears and still says why; it
        # just has no dates, which is the honest rendering of a floor
        # nobody should act on.
        latest_start = _shift(due, -lead)
        latest_shop = _shift(due, -shop_lead)
        earliest = _shift(now, lead)

        # The plan-measured answer: THIS plan took N days from its first
        # cut to its last delivery, so a cut started N days before the
        # due date lands the shipment on time. Shown in preference to
        # the arithmetic date - it carries the queues, the shared
        # machines and the real step times the floor can only estimate.
        pc = plan_cut.get(s["tag"])
        start_by = None
        plan_flow = None
        if pc and s["finish"] and due:
            plan_flow = (s["finish"] - pc).days
            if plan_flow > 0:
                start_by = _shift(due, -plan_flow)
        must_start = start_by or latest_start

        # Verdicts are deliberately TERSE - the first real user asked for
        # less text on this screen, twice. The evidence behind a verdict
        # (the exploded part count, the exact gap) lives in the fold-out.
        if qty_suspect:
            verdict, vclass = "bad BOM data - no floor", "none"
            suspect += 1
            # An unusable floor must not rank, and must not imply a date.
            latest_start = latest_shop = earliest = None
        elif f["kind"] == "none":
            verdict, vclass = "no routing, no supplier", "none"
            no_route += 1
        elif due is None:
            verdict, vclass = "no due date", "none"
        elif earliest is None:
            # The floor overflowed the calendar without tripping the
            # quantity ratio - a lead time of thousands of years from some
            # other data defect. Same treatment as suspect quantities:
            # named, never ranked, never compared against a date.
            verdict, vclass = "bad data - no floor", "none"
            suspect += 1
            latest_start = latest_shop = None
        elif earliest > due:
            gap = (earliest - due).total_seconds() / 86400.0
            verdict = f"cannot meet date ({gap:.0f} d short)"
            vclass = "impossible"
            impossible += 1
        elif must_start and must_start < now:
            verdict, vclass = "must already be running", "tight"
            overdue_start += 1
        else:
            verdict, vclass = "ok", "ok"

        if (s["delay_d"] or 0) > 0:
            late += 1

        m = measured.get("start:%s" % s["tag"]) if fp else None
        rows.append({
            **s,
            "name": ship_names.get(s["tag"], ""),
            # The evidence behind a 'bad BOM data' verdict - shown in the
            # fold-out, not the verdict chip. Formatted here because Django's
            # floatformat only groups thousands when the locale setting is on.
            "peak_qty": f"{f.get('peak_qty', 0):,.0f}" if qty_suspect else "",
            "lead": round(lead, 1),
            "buy_days": round(f.get("buy_days", 0), 1),
            "mach_days": round(f.get("mach_days", 0), 2),
            "mach_hours": round(f.get("mach_hours", 0), 1),
            "transfer_days": round(f.get("transfer_days", 0), 1),
            "pole": f.get("pole", ""),
            "zero_steps": f.get("zero_steps", 0),
            "steps": f.get("steps", 0),
            "latest_start": latest_start,
            "must_start": must_start,
            "start_by": start_by,
            "plan_cut": pc,
            "plan_flow": plan_flow,
            "latest_shop": latest_shop,
            "earliest": earliest,
            "slack": round((must_start - now).total_seconds() / 86400.0, 1)
                     if must_start else None,
            "verdict": verdict,
            "vclass": vclass,
            "delay_days": round(s["delay_d"], 1) if s["delay_d"] else 0,
            # Default the date box to the plan's own start date: the
            # question being measured is "what does starting at the main
            # start date deliver", not the backward latest-start date.
            "suggest": now,
            "measured_text": (
                _measured_sentence(m.get("detail"))
                if m and m.get("fingerprint") == fp else ""
            ),
        })

    # ---- filter, sort, page --------------------------------------------
    show = request.GET.get("show", "all")
    q = (request.GET.get("q") or "").strip()
    # Home-screen and plan-board links arrive as ?dsq=<tag>; same filter.
    if not q:
        q = (request.GET.get("dsq") or "").strip()
    if show == "late":
        rows = [r for r in rows if r["delay_days"] > 0 or r["unplanned"]]
    elif show == "impossible":
        rows = [r for r in rows if r["vclass"] == "impossible"]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r["tag"].lower()]

    # Most urgent first: least slack at the top, no-due-date rows last.
    rows.sort(key=lambda r: (r["slack"] is None, r["slack"] or 0))

    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    keep = [f"show={show}"] + ([f"q={q}"] if q else [])

    # The shipment picker: every DSQ in the book with its human name,
    # read live from the demand table so new shipments appear by itself.
    from freppledb.input.models import Demand

    all_dsq = (
        Demand.objects.using(db)
        .filter(status__in=["open", "quote"])
        .exclude(category=None)
        .values_list("category", flat=True)
        .distinct()
        .order_by("category")
    )
    dsq_options = [(c, ship_names.get(c) or c) for c in all_dsq]

    return render(request, "sieger_ui/startdates.html", {
        "title": _("start dates"),
        "now": now,
        "page": page,
        "total": len(ships),
        "shown": len(rows),
        "impossible": impossible,
        "late": late,
        "overdue_start": overdue_start,
        "no_route": no_route,
        "suspect": suspect,
        "show": show,
        "q": q,
        "dsq_options": dsq_options,
        "querystring": "&" + "&".join(keep),
        "run": _last_run(db),
        "hours_per_day": floor.hours_per_day,
        "days_per_week": floor.days_per_week,
        "efficiency": round(floor.efficiency),
    })
