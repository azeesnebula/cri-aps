"""
Deliver on time - the shipment-first screen.

    GET /delivery/     the promise, what is in the way, and all 34 shipments

Sieger's commitment is fixed: 34 shipments due inside a 4-6 week window.
This screen answers the only question that matters against a fixed date,
in one place and in this order:

  A.  are we making the dates?            (one line)
  B.  what is physically in the way?      (one card per blocking machine)
  C.  each shipment: late by how much, and exactly why

WHERE EVERY NUMBER COMES FROM
-----------------------------
  * The cause lines are the ENGINE'S OWN records (out_constraint): when a
    run cannot place an order on time it writes down which resource was
    full or which supply it waited on. Nothing here re-derives blame.
  * "Needs N more machines" is improve.py's arithmetic, called - not
    copied - so this screen and What-to-do-next can never disagree.
  * The subcontract line is grounded in the model: a vendor route is only
    named when the operations booked on that machine really carry an
    alternate on a vendor resource. Today none do, and the card says so,
    because "no subcontractor modelled - naming one is Sieger's call" is
    the actionable truth, not a gap to paper over.

There is no predicted "days recovered" anywhere. That was tried, was wrong
by a factor of five, and was removed (see improve.py). Capacity arithmetic
is exact; everything else is measured or absent.
"""

import re

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from .improve import _facts, _suggest_machines
from .reports import _db, _rows
from .views import _plan_now

# One row per shipment: the promise and the plan's answer to it.
SHIPS_SQL = """
select coalesce(nullif(category, ''), name) as tag,
       count(*) as orders,
       min(due) as due,
       max(deliverydate) as finish,
       count(*) filter (where deliverydate is null) as unplanned,
       max(extract(epoch from delay)) / 86400.0 as late_days
from demand
group by 1
"""

# The engine's recorded causes, rolled up per shipment. 'await supply'
# owners are internal operation names, useless to a planner one by one -
# they are folded into a single line per shipment. Overloads keep their
# machine, because the machine is the thing someone can act on.
CAUSES_SQL = """
select coalesce(nullif(d.category, ''), d.name) as tag,
       c.name as cause,
       case when c.name = 'overload' then c.owner else '' end as machine,
       count(distinct c.demand) as orders
from out_constraint c
join demand d on d.name = c.demand
group by 1, 2, 3
"""

# The order that drags each shipment latest - the one worth opening in the
# build graph, which already draws its whole work-order -> operations ->
# machines trail.
WORST_SQL = """
select distinct on (coalesce(nullif(category, ''), name))
       coalesce(nullif(category, ''), name) as tag,
       name as order_name
from demand
where deliverydate is not null
order by coalesce(nullif(category, ''), name), delay desc nulls last
"""

# Which machines block shipments, ranked by shipments hit - the damage
# ranking, which is not the utilisation ranking: a machine at 88% can
# block more orders than one at 100% if more routings pass through it.
BLOCKERS_SQL = """
select c.owner as machine,
       count(distinct coalesce(nullif(d.category, ''), d.name)) as ships_hit,
       count(distinct c.demand) as orders_hit
from out_constraint c
join demand d on d.name = c.demand
where c.name = 'overload'
group by 1
order by 2 desc, 3 desc
"""

# A vendor route exists for an operation only when it carries an alternate
# load on a resource living on the vendor calendar. Checked, never assumed.
VENDOR_ALT_SQL = """
select a.resource_id as machine,
       string_agg(distinct coalesce(vr.owner_id, vr.name), ', ') as vendors
from operationresource a
join operationresource x on x.operation_id = a.operation_id
                        and x.resource_id <> a.resource_id
join resource vr on vr.name = x.resource_id
left join resource vo on vo.name = vr.owner_id
where a.resource_id = any(%s)
  and (vr.available_id = 'VENDOR-24X7' or vo.available_id = 'VENDOR-24X7')
group by 1
"""


def blocker_cards(db, only_tag=None):
    """
    One card per overload-causing machine: the damage, the capacity answer
    and the subcontract answer. Shared by this screen and the shipment
    graph so the two can never disagree.

    only_tag narrows the damage counts to one shipment; the capacity and
    subcontract lines stay plant-wide, because a machine is bought or
    subcontracted for the plant, not for one shipment.
    """
    if only_tag:
        blockers = _rows(db, """
            select c.owner as machine,
                   count(distinct c.demand) as orders_hit,
                   1 as ships_hit
            from out_constraint c
            join demand d on d.name = c.demand
            where c.name = 'overload'
              and coalesce(nullif(d.category, ''), d.name) = %s
            group by 1 order by 2 desc
        """, [only_tag])
    else:
        blockers = _rows(db, BLOCKERS_SQL)
    if not blockers:
        return []

    facts = _facts(db)
    by_group = {g["name"]: g for g in facts["groups"]}

    # improve.py's own cards, indexed by group - called, not reimplemented.
    extra_needed = {}
    for card in _suggest_machines(db, facts):
        m = re.search(r"needs (\d+) more", card["title"])
        if m:
            extra_needed[card["group"]] = int(m.group(1))

    vendors = {
        v["machine"]: v["vendors"]
        for v in _rows(db, VENDOR_ALT_SQL,
                       [[b["machine"] for b in blockers]])
    }

    cards = []
    for b in blockers:
        g = by_group.get(b["machine"], {})
        load = g.get("load")
        extra = extra_needed.get(b["machine"])
        # The same three honest cases the template words out - precomputed
        # here so the graph's side rail and delivery.html say one thing.
        if extra:
            fix = (f"needs about {extra} more machine"
                   f"{'s' if extra != 1 else ''} - even running every hour, "
                   f"the current {g.get('machines')} cannot cover the work")
        elif load and load > 100:
            fix = ("more shifts, not more machines - its "
                   f"{g.get('machines')} machine"
                   f"{'s' if g.get('machines') != 1 else ''} can cover this "
                   "by working more hours")
        else:
            fix = ("the hours fit the window - jobs pile up at the same "
                   "moment, so re-sequencing helps before buying does")
        vendor = vendors.get(b["machine"], "")
        cards.append({
            "machine": b["machine"],
            "descr": g.get("description") or "",
            "load": load,
            "machines": g.get("machines"),
            "ships_hit": b["ships_hit"],
            "orders_hit": b["orders_hit"],
            "extra": extra,
            "fix": fix,
            "vendor": vendor,
            "sub": (f"a vendor route exists: {vendor}" if vendor else
                    "no subcontractor modelled - naming one is C.R.I Pumps' "
                    "call, then it can be tested as a what-if"),
        })
    return cards


@login_required(login_url="/data/login/")
def delivery(request):
    db = _db(request)
    now = _plan_now(db)

    # ---- C. the shipments, worst first --------------------------------
    ships = _rows(db, SHIPS_SQL)
    causes = {}
    for c in _rows(db, CAUSES_SQL):
        causes.setdefault(c["tag"], []).append(c)
    worst = {w["tag"]: w["order_name"] for w in _rows(db, WORST_SQL)}

    for s in ships:
        s["late_days"] = round(s["late_days"], 1) if s["late_days"] else 0.0
        s["late"] = bool(s["finish"] and s["due"] and s["finish"] > s["due"])
        s["worst_order"] = worst.get(s["tag"], "")
        rows = sorted(causes.get(s["tag"], []),
                      key=lambda c: -c["orders"])
        s["causes"] = [{
            "line": (
                "%s is overloaded" % c["machine"] if c["machine"]
                else "waiting on earlier jobs to finish"
                if c["cause"] == "await supply"
                else "not enough lead time left"
            ),
            "orders": c["orders"],
        } for c in rows[:5]]
    ships.sort(key=lambda s: -s["late_days"])

    on_time = sum(1 for s in ships if not s["late"] and not s["unplanned"])
    avg_late = (
        round(sum(s["late_days"] for s in ships if s["late"])
              / max(1, sum(1 for s in ships if s["late"])), 1)
    )
    completes = max((s["finish"] for s in ships if s["finish"]), default=None)
    due_lo = min((s["due"] for s in ships if s["due"]), default=None)
    due_hi = max((s["due"] for s in ships if s["due"]), default=None)

    # ---- B. the machines in the way ------------------------------------
    cards = blocker_cards(db)

    return render(request, "sieger_ui/delivery.html", {
        "title": _("deliver on time"),
        "now": now,
        "ships": ships,
        "total": len(ships),
        "on_time": on_time,
        "avg_late": avg_late,
        "completes": completes,
        "due_lo": due_lo,
        "due_hi": due_hi,
        "cards": cards,
    })
