"""
Stamp the shipment (DSQ) tag onto plan rows after every replan.

frePPLe's grids filter by the fields a row carries, and operation plans
carry no shipment field - the pegging lives in a JSON plan field the
grids cannot query. Stamping the tag into operationplan.name (and
appending it to constraint descriptions) lets the standard screens
filter by shipment with their own native URL filters:

    /data/input/manufacturingorder/?name__contains=<tag>   the schedule
    /data/input/purchaseorder/?name__contains=<tag>        purchase orders
    /constraint/?description__contains=[DSQ <tag>          why late

The engine erases and regenerates the plan on every run, so the stamp
is refreshed by a post-save signal on the runplan Task (see
signals.py). A plan shared by several shipments carries ONE tag - the
first the pegging offers - noted here rather than hidden.

Problem rows are plant-level (a machine, a buffer) and belong to no
single shipment; the shipment's own exceptions are on its analysis
page instead.
"""

STAMP_OPERATIONPLAN_SQL = """
update operationplan op
set name = sub.tag
from (
  select op2.reference, min(d.category) as tag
  from operationplan op2
  join lateral jsonb_object_keys(
       coalesce(op2.plan->'pegging', '{}'::jsonb)) k(d) on true
  join demand d on d.name = k.d
  where d.category is not null and d.category <> ''
  group by 1
) sub
where op.reference = sub.reference
"""

STAMP_CONSTRAINT_SQL = """
update out_constraint c
set description = sub.stamp
from (
  select c2.id, c2.description || ' [DSQ ' || min(d.category) || ']'
         as stamp
  from out_constraint c2
  join demand d on d.name = c2.demand
  where d.category is not null and d.category <> ''
    and coalesce(c2.description, '') not like '%[DSQ %'
  group by c2.id, c2.description
) sub
where c.id = sub.id
"""


def stamp_shipments(db):
    """Apply the DSQ tags to the current plan. Safe to re-run."""
    from django.db import connections

    with connections[db].cursor() as c:
        c.execute(STAMP_OPERATIONPLAN_SQL)
        ops = c.rowcount
        c.execute(STAMP_CONSTRAINT_SQL)
        rows = c.rowcount
    return ops, rows
