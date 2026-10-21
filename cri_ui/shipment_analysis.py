"""Read-only explanations of a saved shipment plan, not a new feasibility solve.

Constraint records describe conflicts encountered by the solver. They are not
proof of the final critical path, nor predictions of what a proposed fix saves.
"""

import re
from urllib.parse import quote, urlencode


ORDERS_SQL = """
select d.name, d.item_id, coalesce(i.description, '') as item_descr,
       coalesce(nullif(d.category, ''), d.name) as shipment_tag,
       d.quantity, d.plannedquantity, d.due, d.deliverydate, d.maxlateness,
       extract(epoch from (d.deliverydate - d.due)) / 86400.0 as late_d
from demand d
left join item i on i.name = d.item_id
where coalesce(nullif(d.category, ''), d.name) = coalesce(%s, nullif(d.category, ''), d.name)
  and d.status in ('open', 'quote')
order by d.deliverydate desc nulls last, d.name
"""

CONSTRAINTS_SQL = """
select c.demand, c.name as kind, c.entity, c.owner,
       c.startdate, c.enddate, c.description
from out_constraint c
join demand d on d.name = c.demand
where coalesce(nullif(d.category, ''), d.name) = coalesce(%s, nullif(d.category, ''), d.name)
  and d.status in ('open', 'quote')
order by c.demand, c.startdate, c.id
"""

TRACE_SQL = """
select op.reference, op.operation_id, o.description, op.item_id,
       op.startdate, op.enddate, op.status, op.quantity,
       (select string_agg(distinct opr.resource_id, ', ' order by opr.resource_id)
        from operationplanresource opr where opr.operationplan_id = op.reference
        ) as machines
from operationplan op
left join operation o on o.name = op.operation_id
where op.type = 'WO' and op.plan->'pegging' ?| (
  select coalesce(array_agg(d.name), array[]::varchar[]) from demand d
  where coalesce(nullif(d.category, ''), d.name) = coalesce(%s, nullif(d.category, ''), d.name)
    and d.status in ('open', 'quote'))
order by op.enddate desc, op.startdate desc, op.reference
"""

SUPPLY_SQL = """
select op.reference, op.type, op.item_id, i.description, op.quantity,
       op.supplier_id, op.origin_id, op.location_id,
       op.startdate, op.enddate, op.status
from operationplan op
left join item i on i.name = op.item_id
where op.type in ('PO', 'DO') and op.plan->'pegging' ?| (
  select coalesce(array_agg(d.name), array[]::varchar[]) from demand d
  where coalesce(nullif(d.category, ''), d.name) = coalesce(%s, nullif(d.category, ''), d.name)
    and d.status in ('open', 'quote'))
order by op.enddate nulls first, op.reference
"""

FRESHNESS_SQL = """
select count(*) as linked_steps,
       count(*) filter (where op.startdate < %s
         and coalesce(op.status, '') not in ('completed', 'closed')) as past_steps,
       min(op.startdate) as first_start
from operationplan op
where op.type = 'WO' and op.plan->'pegging' ?| (
  select coalesce(array_agg(d.name), array[]::varchar[]) from demand d
  where coalesce(nullif(d.category, ''), d.name) = coalesce(%s, nullif(d.category, ''), d.name)
    and d.status in ('open', 'quote'))
"""

# Loading an existing plan into the web service is not generating a new plan.
LAST_SUPPLY_SQL = """
select id, finished, arguments from execute_log
where name = 'runplan' and status = 'Done' and finished is not null
  and arguments ~ '(^|[=, ])supply([, ]|$)'
order by id desc limit 1
"""

SUPPLIERS_SQL = """
select item_id, supplier_id, location_id, leadtime, priority,
       effective_start, effective_end
from itemsupplier where item_id = any(%s)
order by item_id, priority, supplier_id
"""

STOCK_SQL = """
select item_id, location_id, sum(onhand) as onhand
from buffer where item_id = any(%s)
group by item_id, location_id order by item_id, location_id
"""


def _url(prefix, path, **params):
    return prefix + path + (('?' + urlencode(params)) if params else '')


def delivery_position(orders):
    """Missing dates/quantities must never silently become an on-time promise."""
    for order in orders:
        finish, due = order['deliverydate'], order['due']
        order['late_d'] = (finish - due).total_seconds() / 86400 if finish and due else None
        order['state'] = (
            'unplanned' if finish is None else
            'unknown' if due is None else
            'late' if finish > due else 'on time'
        )
        order['short'] = finish is not None and (
            order['plannedquantity'] is None or
            order['plannedquantity'] < order['quantity']
        )
    counts = {state: sum(o['state'] == state for o in orders)
              for state in ('unplanned', 'unknown', 'late', 'on time')}
    short = sum(o['short'] for o in orders)
    incomplete = bool(counts['unplanned'] or counts['unknown'] or short)
    if counts['late']:
        verdict, tone = 'Target will be missed in the saved plan', 'late'
    elif incomplete or not orders:
        verdict, tone = 'On-time delivery is not established', 'unknown'
    else:
        verdict, tone = 'On time in the saved plan', 'ok'
    scheduled = [o for o in orders if o['deliverydate'] is not None]
    latest = max((o['deliverydate'] for o in scheduled), default=None)
    last_orders = [o for o in scheduled if o['deliverydate'] == latest]
    return {
        'total': len(orders), 'on_time': counts['on time'],
        'late': counts['late'], 'unplanned': counts['unplanned'],
        'unknown': counts['unknown'], 'short': short, 'incomplete': incomplete,
        'verdict': verdict, 'tone': tone, 'finish': latest,
        'worst_days': max((o['late_d'] or 0 for o in orders), default=0),
        'latest_order': last_orders[0] if last_orders else None,
        'latest_count': len(last_orders),
    }


def group_blockers(records, orders, prefix=''):
    """Group exact recorded owners, preserving order links and conflict dates."""
    by_order = {o['name']: o for o in orders}
    groups = {}
    for row in records:
        order = by_order.get(row['demand'])
        if order is None:
            continue
        key = (row['kind'], row['entity'], row['owner'])
        if key not in groups:
            groups[key] = {
                'kind': row['kind'], 'entity': row['entity'],
                'owner': row['owner'], 'orders': {}, 'records': [],
                'links': [], 'material': None,
            }
        group = groups[key]
        group['orders'][order['name']] = order
        group['records'].append(row)
    result = list(groups.values())
    for group in result:
        group['orders'] = list(group['orders'].values())
        group['late'] = sum(o['state'] == 'late' for o in group['orders'])
        group['unplanned'] = sum(o['state'] == 'unplanned' for o in group['orders'])
        group['on_time'] = sum(o['state'] == 'on time' for o in group['orders'])
        group['short'] = sum(o['short'] for o in group['orders'])
        group['first_conflict'] = min((r['startdate'] for r in group['records']
                                       if r['startdate']), default=None)
        group['last_conflict'] = max((r['enddate'] for r in group['records']
                                      if r['enddate']), default=None)
        group['description'] = next((r.get('description') for r in group['records']
                                      if r.get('description')), 'No engine description saved.')
        group['links'].append({
            'label': 'Constraint records',
            'url': _url(prefix, '/constraint/', owner=group['owner'], name=group['kind']),
        })
        # Parse only the engine's explicit purchase-operation naming format.
        # Never treat c.item (the demanded finished part) as the purchased item.
        purchase = re.fullmatch(
            r'Purchase (.+) @ (.+) from (.+) valid from .+', group['owner'] or ''
        )
        if purchase:
            item, location, supplier = purchase.groups()
            group['material'] = {
                'item': item, 'location': location, 'supplier': supplier,
                'stock': [], 'suppliers': [],
            }
            group['links'].extend([
                {'label': 'Material stock', 'url': _url(
                    prefix, '/data/input/buffer/', item=item, location=location)},
                {'label': 'Purchase receipts', 'url': _url(
                    prefix, '/data/input/purchaseorder/', item=item, location=location)},
                {'label': 'Supply rules', 'url': _url(
                    prefix, '/data/input/itemsupplier/', item=item)},
            ])
        elif group['entity'] == 'capacity':
            group['links'].append({
                'label': 'Machine load and calendar',
                'url': prefix + '/resource/' + quote(group['owner'] or '', safe='') + '/',
            })
        elif group['entity'] == 'operation':
            group['links'].append({
                'label': 'Operation / routing',
                'url': _url(prefix, '/data/input/operation/', name=group['owner']),
            })
        if group['material']:
            group['summary'] = 'Purchase lead time restricted material supply in the recorded planning window.'
            group['next_check'] = 'Confirm stock at the required location, supplier lead time and receipt dates.'
        elif group['entity'] == 'capacity':
            group['summary'] = 'Required machine capacity was unavailable in the recorded planning window.'
            group['next_check'] = 'Review the machine calendar, competing work and available alternate capacity.'
        elif group['kind'] == 'manufacturing lead time':
            group['summary'] = 'The production routing could not fit inside the recorded planning window.'
            group['next_check'] = 'Check routing duration, predecessor completion and the earliest feasible start.'
        else:
            group['summary'] = group['description']
            group['next_check'] = 'Review the recorded conflict window against the affected orders and their supply dates.'
    result.sort(key=lambda g: (-g['unplanned'], -g['late'], g['kind'], g['owner'] or ''))
    for index, group in enumerate(result, 1):
        group['anchor'] = 'blocker-%d' % index
    return result


def shipment_analysis(db, tag, rows, wall_now, prefix=''):
    # A null tag means all shipments; an exact tag retains the same scoped UI.
    orders = rows(db, ORDERS_SQL, [tag])
    position = delivery_position(orders)
    for order in orders:
        order['graph_url'] = prefix + '/build-graph/' + quote(order['name'], safe='') + '/'
        order['constraint_url'] = _url(prefix, '/constraint/', demand=order['name'])
        order['blockers'] = []
    blockers = group_blockers(rows(db, CONSTRAINTS_SQL, [tag]), orders, prefix)
    for group in blockers:
        for order in group['orders']:
            order['blockers'].append({
                'anchor': group['anchor'], 'kind': group['kind'], 'owner': group['owner'],
                'subject': group['material']['item'] if group['material'] else group['owner'],
                'label': ('Material' if group['material'] else
                          'Capacity' if group['entity'] == 'capacity' else
                          'Routing' if group['kind'] == 'manufacturing lead time' else
                          group['kind']),
            })

    items = sorted({g['material']['item'] for g in blockers if g['material']})
    if items:
        suppliers = rows(db, SUPPLIERS_SQL, [items])
        stocks = rows(db, STOCK_SQL, [items])
        for group in blockers:
            material = group['material']
            if not material:
                continue
            material['stock'] = [s for s in stocks if s['item_id'] == material['item']
                                 and s['location_id'] == material['location']]
            material['suppliers'] = [s for s in suppliers
                                    if s['item_id'] == material['item']
                                    and s['supplier_id'] == material['supplier']
                                    and s['location_id'] in (None, material['location'])]

    steps = rows(db, TRACE_SQL, [tag])
    for step in steps:
        step['url'] = _url(prefix, '/data/input/manufacturingorder/',
                            reference=step['reference'])
    supplies = rows(db, SUPPLY_SQL, [tag])
    for supply in supplies:
        supply['url'] = _url(prefix, '/data/input/%s/' % (
            'purchaseorder' if supply['type'] == 'PO' else 'distributionorder'),
            reference=supply['reference'])
    progress = execution_position(steps, wall_now)
    supply_position = execution_position(supplies, wall_now)
    freshness = rows(db, FRESHNESS_SQL, [wall_now, tag])[0]
    last_supply = rows(db, LAST_SUPPLY_SQL)
    return {
        'position': position, 'blockers': blockers, 'orders': orders,
        'late_orders': sorted((o for o in orders if o['state'] == 'late'),
                              key=lambda o: o['late_d'], reverse=True),
        'unplanned_orders': [o for o in orders if o['state'] == 'unplanned'],
        'uncertain_orders': [o for o in orders if o['short'] or o['state'] == 'unknown'],
        'trace_steps': steps, 'trace_total': len(steps), 'progress': progress,
        'supplies': supplies, 'supply_position': supply_position,
        'blocker_kinds': sorted({b['kind'] for b in blockers}),
        'freshness': freshness, 'checked_at': wall_now,
        'last_supply': last_supply[0] if last_supply else None,
        'without_evidence': sum(not o['blockers'] for o in orders
                                if o['state'] in ('late', 'unplanned')),
    }


def execution_position(records, now):
    """Status is evidence of execution; a date in the past isn't completion."""
    for row in records:
        row['complete'] = row['status'] in ('completed', 'closed')
        row['overdue'] = bool(not row['complete'] and row['enddate'] and row['enddate'] < now)
    complete = sum(r['complete'] for r in records)
    return {
        'total': len(records), 'complete': complete,
        'open': len(records) - complete,
        'overdue': sum(r['overdue'] for r in records),
        'pct': round(100 * complete / len(records)) if records else 0,
        'first_start': min((r['startdate'] for r in records if r['startdate']), default=None),
        'last_finish': max((r['enddate'] for r in records if r['enddate']), default=None),
    }
