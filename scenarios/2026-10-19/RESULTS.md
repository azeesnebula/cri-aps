# Saved C.R.I schedule — results

The constrained supply plan is loaded and saved in the **C.R.I `cri0` database**, served at http://localhost:8081.

## Outcome

| Measure | Result |
|---|---:|
| Planning reference date | 19 October 2026, 06:00 Asia/Kolkata |
| Shipments planned in full and on time | **10 / 10** |
| Independent finished-product order lines | 20 |
| Finished pumps | **670** |
| Product families | 5 |
| Manufacturing orders | 120 |
| Scheduled machine operations | **260** |
| Staffed machines modeled | 12 (10 used; two alternate cells remain available) |
| Active machine workload, excluding breaks/nonworking time | **1,768.243 hours** |
| First scheduled production | 21 October 2026, 10:56:29 |
| Final dispatch | 24 November 2026, 17:00 |
| Independent feasibility checks | **15 / 15 passed** |

The plan reference date remains 19 October. The engine schedules backwards from due dates to avoid unnecessary early production, so the first manufacturing operation is on 21 October. Supplier commitments can have been placed before the plan reference date; **no new manufacturing starts before it**.

## Shipment commitments

All dates below are **ex-factory dispatch deadlines in 2026, at 17:00 India time**. The saved dispatch date matches the final due date for both lines in every shipment.

| Shipment | Fictitious destination/use | Pump mix | Quantity | Due / planned dispatch |
|---|---|---|---:|---|
| CRI-SHIP-001 | Coimbatore residential distributor | Mini 60; monoblock 20 | 80 | 28 October |
| CRI-SHIP-002 | Erode agricultural distributor | Borewell 40; openwell 30 | 70 | 30 October |
| CRI-SHIP-003 | Chennai housing contractor | Mini 70; monoblock 20 | 90 | 3 November |
| CRI-SHIP-004 | Madurai irrigation project | Borewell 40; openwell 30 | 70 | 5 November |
| CRI-SHIP-005 | Bengaluru water treatment integrator | Vertical multistage 20; mini 20 | 40 | 7 November |
| CRI-SHIP-006 | Salem agricultural distributor | Openwell 40; monoblock 30 | 70 | 14 November |
| CRI-SHIP-007 | Kochi commercial buildings | Vertical multistage 20; borewell 30 | 50 | 17 November |
| CRI-SHIP-008 | Hyderabad residential distributor | Mini 60; monoblock 30 | 90 | 19 November |
| CRI-SHIP-009 | Pune process-water integrator | Vertical multistage 20; openwell 30 | 50 | 21 November |
| CRI-SHIP-010 | Tiruppur industrial water project | Borewell 40; vertical multistage 20 | 60 | 24 November |

## What was verified

`validation.json` records the completed planning task, unchanged final input dates, exact scenario counts, full quantities, on-time dispatch, all-together shipment lines, no pre-plan production, finite leaf-machine assignments, no simultaneous jobs on a single-capacity machine, working shifts/breaks/maintenance, cycle time/setup/85% efficiency, routing precedence, nonnegative material balances and purchasing lead times.

The machine-capacity export independently intersects scheduled work with the staffed calendar. **Hydraulic preparation** is the largest load at 305.498 hours; **winding** follows at 289.804 hours. The schedule does not force extra work onto idle machines merely to increase utilization.

The original provisional-date capacity check is preserved in `trial-capacity-check/`. Its six late shipments were used to derive the final promises, as described in `README.md`. The final run is feasible within the documented planning assumptions; it is not a claim of a globally optimal schedule or a real C.R.I delivery promise.

## Open the plan

- [Production overview](http://localhost:8081/)
- [Full machine board: 19 October onward](http://localhost:8081/machine-schedule/?start=2026-10-19&days=45)
- [Production orders](http://localhost:8081/production-orders/)
- [First shipment analysis](http://localhost:8081/shipment/CRI-SHIP-001/)
- [Build graph](http://localhost:8081/build-graph/)

## Deliverables

- `CRI_Planning_Inputs_2026-10-19.xlsx` — source register, calendars, resources, suppliers, products, multi-level BOM, routings, capacity links, opening inventory, confirmed receipts and orders.
- `planning_inputs.json` — machine-readable snapshot of the same inputs; duration fields use seconds.
- `CRI_Constrained_Schedule_2026-10-19.xlsx` — shipment commitments, order lines, machine assignments, active shift segments, capacity, purchase orders, material movements and validation.
- `machine_schedule.csv` — full operation-by-operation machine schedule, with active and elapsed hours explicitly separated.
- `active_shift_segments.csv` — each actual working segment, split around breaks, nights, Sundays and maintenance.
- `shipments.csv`, `machine_capacity.csv`, `validation.json` — compact planning summaries and independent checks.
- `production_overview.png`, `machine_plan_board.png` — the populated live application.

The built-in archive snapshot for 19 October 2026 has been refreshed to the final plan. The existing Sieger instance and its database were not used for this scenario.
