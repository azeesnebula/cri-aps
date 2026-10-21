"""Pure saved-plan explanation tests; no database or solver is started."""

from datetime import datetime, timedelta, timezone
import unittest
from urllib.parse import parse_qs, urlsplit

from cri_ui.shipment_analysis import (
    delivery_position, group_blockers, shipment_analysis, execution_position,
    ORDERS_SQL, CONSTRAINTS_SQL, TRACE_SQL, FRESHNESS_SQL, LAST_SUPPLY_SQL,
    SUPPLIERS_SQL, STOCK_SQL, SUPPLY_SQL,
)


DUE = datetime(2026, 10, 29, tzinfo=timezone.utc)


def order(name, days=0, quantity=48, planned=48):
    finish = None if days is None else DUE + timedelta(days=days)
    return dict(name=name, item_id='FINISHED', quantity=quantity,
                plannedquantity=planned, due=DUE, deliverydate=finish,
                late_d=days)


def constraint(demand, owner=None):
    return dict(demand=demand, kind='purchasing lead time', entity='operation',
                owner=owner or 'Purchase RAW @ SITE from VENDOR valid from 2020-01-01',
                startdate=DUE - timedelta(days=60), enddate=DUE - timedelta(days=55))


class DeliveryPositionTests(unittest.TestCase):
    def test_unplanned_does_not_hide_scheduled_lateness(self):
        data = [order('early', -1), order('late', 82), order('missing', None, planned=0)]
        result = delivery_position(data)
        self.assertEqual((result['on_time'], result['late'], result['unplanned']), (1, 1, 1))
        self.assertEqual(result['tone'], 'late')
        self.assertTrue(result['incomplete'])
        self.assertEqual(result['latest_order']['name'], 'late')
        self.assertEqual(result['worst_days'], 82)

    def test_short_or_unknown_quantity_cannot_be_promised(self):
        for planned in (0, 47, None):
            with self.subTest(planned=planned):
                result = delivery_position([order('partial', -1, planned=planned)])
                self.assertEqual(result['tone'], 'unknown')
                self.assertEqual(result['short'], 1)

    def test_missing_dates_and_empty_plan_are_not_on_time(self):
        self.assertEqual(delivery_position([])['tone'], 'unknown')
        self.assertEqual(delivery_position([order('unplanned', None)])['tone'], 'unknown')
        missing_due = order('no target')
        missing_due['due'] = None
        self.assertEqual(delivery_position([missing_due])['unknown'], 1)

    def test_actual_dates_override_stale_delay_and_respect_each_due_date(self):
        first, second = order('first'), order('second', 5)
        second['due'] = DUE + timedelta(days=6)
        second['late_d'] = -1
        first['delay'] = timedelta(days=90)  # not a reliable date comparison
        result = delivery_position([first, second])
        self.assertEqual(result['tone'], 'ok')
        self.assertEqual(result['on_time'], 2)

    def test_tied_latest_orders_and_small_real_delay(self):
        result = delivery_position([order('a', 0.001), order('b', 0.001)])
        self.assertEqual(result['latest_count'], 2)
        self.assertEqual(result['late'], 2)


class BlockerTests(unittest.TestCase):
    def test_group_deduplicates_orders_but_preserves_conflicts(self):
        orders = [order('late', 10), order('missing', None), order('ok', -1)]
        delivery_position(orders)
        records = [constraint('late'), constraint('late'), constraint('missing'), constraint('ok')]
        groups = group_blockers(records, orders, '/scenario1')
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual((group['late'], group['unplanned'], group['on_time']), (1, 1, 1))
        self.assertEqual(len(group['records']), 4)
        self.assertEqual(group['material']['item'], 'RAW')
        self.assertTrue(all(link['url'].startswith('/scenario1/') for link in group['links']))

    def test_unknown_owner_does_not_invent_material(self):
        orders = [order('missing', None)]
        delivery_position(orders)
        group = group_blockers([constraint('missing', 'Unrecognized purchase operation')], orders)[0]
        self.assertIsNone(group['material'])

    def test_query_values_are_encoded(self):
        orders = [order('missing', None)]
        delivery_position(orders)
        owner = 'Purchase A&B @ SITE from VENDOR valid from 2020-01-01'
        group = group_blockers([constraint('missing', owner)], orders)[0]
        params = parse_qs(urlsplit(group['links'][0]['url']).query)
        self.assertEqual(params['owner'], [owner])

    def test_analysis_uses_selected_database_and_scoped_material_evidence(self):
        orders = [order('late', 82), order('missing', None)]
        responses = {
            ORDERS_SQL: orders, CONSTRAINTS_SQL: [constraint('missing')],
            TRACE_SQL: [], SUPPLY_SQL: [], FRESHNESS_SQL: [dict(past_steps=1, linked_steps=2)],
            LAST_SUPPLY_SQL: [],
            SUPPLIERS_SQL: [dict(item_id='RAW', supplier_id='VENDOR', location_id=None),
                            dict(item_id='RAW', supplier_id='OTHER', location_id=None)],
            STOCK_SQL: [dict(item_id='RAW', location_id='OTHER', onhand=999)],
        }

        def read_rows(db, sql, params=None):
            self.assertEqual(db, 'scenario1')
            self.assertTrue(sql.lstrip().lower().startswith('select'))
            if sql in (ORDERS_SQL, CONSTRAINTS_SQL):
                self.assertEqual(params, ['SHIP'])
            if sql == TRACE_SQL:
                self.assertEqual(params, ['SHIP'])
            return responses[sql]

        result = shipment_analysis('scenario1', 'SHIP', read_rows, DUE, '/scenario1')
        self.assertEqual(result['without_evidence'], 1)
        self.assertEqual(result['blockers'][0]['material']['stock'], [])
        self.assertEqual(len(result['blockers'][0]['material']['suppliers']), 1)
        self.assertEqual(result['unplanned_orders'][0]['blockers'][0]['anchor'], 'blocker-1')
        self.assertIsNone(result['last_supply'])

    def test_all_shipments_keeps_order_states_and_uses_unfiltered_scope(self):
        orders = [order('shipment-a', -1), order('shipment-b', 82),
                  order('shipment-b-unplanned', None)]

        def read_rows(db, sql, params=None):
            if sql == ORDERS_SQL:
                self.assertEqual(params, [None])
                return orders
            if sql == CONSTRAINTS_SQL:
                self.assertEqual(params, [None])
                return []
            if sql in (TRACE_SQL, SUPPLY_SQL):
                self.assertEqual(params, [None])
                return []
            if sql == FRESHNESS_SQL:
                self.assertEqual(params, [DUE, None])
                return [dict(past_steps=0, linked_steps=0)]
            if sql == LAST_SUPPLY_SQL:
                return []
            self.fail('Unexpected query')

        result = shipment_analysis('default', None, read_rows, DUE)
        self.assertEqual(result['position']['total'], 3)
        self.assertEqual(result['position']['on_time'], 1)
        self.assertEqual(result['position']['late'], 1)
        self.assertEqual(result['position']['unplanned'], 1)
        self.assertTrue(result['position']['incomplete'])

    def test_every_blocker_is_retained_with_exact_impact_and_dates(self):
        orders = [order('late', 10), order('ok', -1)]
        delivery_position(orders)
        records = [dict(constraint('late', 'Machine %d' % i),
                        kind='overload', entity='capacity', description='Capacity conflict')
                   for i in range(31)]
        records.append(dict(records[0], demand='ok'))
        groups = group_blockers(records, orders)
        self.assertEqual(len(groups), 31)
        first = next(g for g in groups if g['owner'] == 'Machine 0')
        self.assertEqual((first['late'], first['on_time']), (1, 1))
        self.assertEqual(first['first_conflict'], DUE - timedelta(days=60))
        self.assertEqual(first['last_conflict'], DUE - timedelta(days=55))
        self.assertEqual(first['description'], 'Capacity conflict')
        self.assertIn('calendar', first['next_check'])

    def test_steps_are_loaded_even_when_all_demand_is_unplanned(self):
        steps = [dict(reference='shared-step', status='confirmed',
                      startdate=DUE - timedelta(days=2), enddate=DUE - timedelta(days=1))]
        responses = {
            ORDERS_SQL: [order('unplanned', None)], CONSTRAINTS_SQL: [],
            TRACE_SQL: steps, SUPPLY_SQL: [], LAST_SUPPLY_SQL: [],
            FRESHNESS_SQL: [dict(past_steps=1, linked_steps=1)],
        }
        def read_rows(db, sql, params=None):
            if sql in (TRACE_SQL, SUPPLY_SQL):
                self.assertEqual(params, ['different shipment / &'])
            return responses[sql]
        result = shipment_analysis('scenario2', 'different shipment / &', read_rows, DUE, '/scenario2')
        self.assertEqual(result['trace_total'], 1)
        self.assertEqual(result['progress']['overdue'], 1)
        self.assertTrue(result['trace_steps'][0]['url'].startswith('/scenario2/'))


class ExecutionPositionTests(unittest.TestCase):
    def test_past_dates_are_not_evidence_of_completion(self):
        records = [dict(status=status, startdate=DUE - timedelta(days=2),
                        enddate=DUE - timedelta(days=1))
                   for status in ('proposed', 'confirmed', 'completed', 'closed')]
        records.append(dict(status=None, startdate=None, enddate=None))
        result = execution_position(records, DUE)
        self.assertEqual((result['total'], result['complete'], result['overdue']), (5, 2, 2))
        self.assertEqual(result['pct'], 40)
        self.assertEqual(result['open'], 3)

    def test_empty_progress_is_explicit(self):
        result = execution_position([], DUE)
        self.assertEqual(result['total'], 0)
        self.assertIsNone(result['last_finish'])


if __name__ == '__main__':
    unittest.main()
