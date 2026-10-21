# C.R.I Pumps scheduling scenario

**Plan date:** Monday 19 October 2026, 06:00, Asia/Kolkata.  
**Scope:** 10 fictional shipments, 20 order lines, 670 finished pumps, five product families.

This is a complete **illustrative planning example**, not C.R.I's actual order book, technical BOM, factory layout or time study. Public C.R.I product pages establish the product families and general construction only. All SKUs, power/stage variants, customers, suppliers, quantities, costs, timings, machine assignments, material stocks and due dates are invented for this scenario.

## Information a reliable production schedule requires

| Input | Included in this scenario | Replace with real operational data for live planning |
|---|---|---|
| Orders and priorities | 10 shipments / 20 independent finished-pump demand lines, equal priority, full-quantity all-together shipment policy | Customer purchase orders, revision, priority and contractual dates |
| Delivery definition | Ex-factory dispatch cutoff at 17:00 local, 28 Oct–24 Nov | Customer arrival promise, transit duration and dispatch calendar |
| Products | Mini, 100 mm borewell, openwell, centrifugal monoblock, vertical multistage inline | Approved SKU/variant and engineering specification |
| BOM and make/buy | Shaft, casing, hydraulic cartridge, stator, motor, finished pump; purchased blanks, components and packaging | Revision-controlled engineering BOM, alternates, actual quantities and yields |
| Routing | Shaft turn/grind; casing CNC/deburr; hydraulic finish/build; wind/electrical test; motor assembly/test; final assembly/wet test/pack | Approved predecessors, transfer batches, quality gates and outsourcing |
| Time standards | Fixed setup per lot plus per-unit cycle time; all manufactured lots up to 100 to support full-quantity all-together shipping | Measured setup/run times, sequence-dependent changeovers and curing times |
| Capacity | 12 staffed single-job cells, finite machine capacity, 85% efficiency | Actual machines, qualifications, operator rosters, shared labor and tooling |
| Working calendar | Mon–Sat, 06–14 and 14–22; two 30-minute breaks; Sunday off | Plant-approved shifts, holidays, leave and overtime |
| Maintenance | TEST-01: 28 Oct 14–18; CNC-01: 3 Nov 06–10 | Confirmed maintenance and breakdown outlook |
| Inventory | 35% of exploded bought-part demand as opening stock; no made-item stock | Counted usable stock, quality holds, reservations and WIP |
| Procurement | 35% confirmed receipts on 22/23 Oct; remainder proposed with 2–6 calendar-day lead times and pack multiples | Supplier confirmations, supplier capacity and actual lead times |
| Quality | Explicit electrical/hydraulic test steps; 100% piece yield; 3% steel and 2% wire allowance | Scrap, rework, inspection failure and retest data |
| Baseline | Supply plan, independent feasibility checks and archive snapshot | Frozen/released operations, actual starts/completions and replanning rules |

The modeled factory buys foundry blanks, rotor kits and hydraulic-stage blanks rather than scheduling a foundry or polymer-moulding plant. Each machine cell includes its dedicated operator; shared labor and supplier capacity are not independently constrained. No public holiday calendar or existing competing order book was supplied. These assumptions are explicit so the resulting feasible plan is not mistaken for a mathematical guarantee or an actual C.R.I commitment.

## How due dates were selected

The first trial used 26 October–16 November. Its full finite-capacity run produced all 670 pumps, passed the machine/material/precedence/calendar checks, but met only four of ten trial shipment dates. That unaltered run is retained in `trial-capacity-check/`.

The final promises are at least **two Monday–Saturday working days after the trial's capacity-feasible finish**, with at least **two working days between successive shipment commitments**. This is capacity-based promise setting for invented orders, not a claim that the original trial promises were met. The final plan is rerun against those fixed promises; the exporter does not change due dates. The engine schedules backwards from due dates, so the due-date allowance is not necessarily idle time between packing and dispatch.

## Sources

- [C.R.I product range](https://www.crigroups.com/pumps/)
- [Mini pumps — material and power range information](https://www.crigroups.com/pumps/mini-pumps/)
- [Borewell pumps — multi-stage and motor construction](https://www.crigroups.com/borewell-submersible-pumps/)
- [Openwell submersible pumps](https://www.crigroups.com/pump/openwell-submersible-pumps/)
- [Centrifugal monoblock pumps](https://www.crigroups.com/pump/centrifugal-monoblock-pumps/)
- [Vertical multistage inline pumps](https://www.crigroups.com/pump/vertical-multistage-inline-pump/)

## Reproduce

Run from the `C.R.I Pumps` project directory. `--load` intentionally refuses any non-empty planning dataset.

```powershell
docker compose up -d --build --wait
docker compose exec web frepplectl criplanning --load
docker compose exec web frepplectl runplan --constraint=capa,mfg_lt,po_lt --plantype=1 --env=supply,nowebservice --database=default
docker compose exec web frepplectl criplanning --export
docker compose exec web frepplectl archive --database=default --force
docker compose cp web:/var/log/frepple/cri-planning-20261019/. "scenarios/2026-10-19/"
```

For an existing scenario, run the last four commands only. Inputs and fixed final due dates are not changed by the export/validation step. `archive --force` refreshes this plan-date snapshot after trial runs. Generated XLSX files are local deliverables (ignored by the repository's existing customer-data rules); the source generator and JSON/CSV outputs document the scenario reproducibly.
