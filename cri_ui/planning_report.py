"""Independent feasibility checks and usable planning exports for the demo."""
from collections import defaultdict
from datetime import datetime, timedelta
import csv
import json
from pathlib import Path

from django.db import connections
from freppledb.common.models import Parameter
from freppledb.execute.models import Task
from freppledb.input.models import (
    Demand, OperationPlan, OperationPlanResource, Resource,
)
from freppledb.output.models import Constraint, Problem

from .planning_scenario import (
    SOURCE, START, SHIFTS, SHIPMENTS, MAINTENANCE,
    json_default, scalar, write_workbook,
)


def rows(db, sql, args=None):
    with connections[db].cursor() as cursor:
        cursor.execute(sql, args or [])
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, r)) for r in cursor.fetchall()]


def subtract(intervals, cut_start, cut_end):
    result = []
    for start, end in intervals:
        if cut_end <= start or cut_start >= end:
            result.append((start, end))
        else:
            if start < cut_start:
                result.append((start, cut_start))
            if cut_end < end:
                result.append((cut_end, end))
    return result


def calendar_segments(machine, start, end):
    result = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end:
        if day.weekday() < 6:
            for h1, m1, h2, m2 in SHIFTS:
                left = max(start, START, day.replace(hour=h1, minute=m1))
                right = min(end, day.replace(hour=h2, minute=m2))
                if left < right:
                    result.append((left, right))
        day += timedelta(days=1)
    if machine in MAINTENANCE:
        result = subtract(result, *MAINTENANCE[machine])
    return result


def active_segments(op):
    result = [(op.startdate, op.enddate)]
    for left, right in (op.plan or {}).get("interruptions", []):
        result = subtract(result, datetime.fromisoformat(left), datetime.fromisoformat(right))
    return result


def seconds(intervals):
    return sum((right - left).total_seconds() for left, right in intervals)


def export_plan(path, db="default"):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    demand_rows = list(Demand.objects.using(db).filter(source=SOURCE).order_by("due", "name").values(
        "name", "category", "customer_id", "item_id", "quantity", "plannedquantity", "due", "deliverydate", "priority"))
    shipments = defaultdict(list)
    for row in demand_rows:
        row["on_time_full"] = bool(row["deliverydate"] and row["deliverydate"] <= row["due"]
                                   and row["plannedquantity"] >= row["quantity"])
        shipments[row["category"]].append(row)
    names = json.loads(Parameter.getValue("sieger.shipnames", db, "{}"))
    horizon = max((r["due"] for r in demand_rows), default=START).replace(hour=0) + timedelta(days=1)
    last_delivery = max((r["deliverydate"] for r in demand_rows if r["deliverydate"]), default=START)
    horizon = max(horizon, last_delivery.replace(hour=0, minute=0, second=0) + timedelta(days=1))
    machine_rows, segments = [], []
    events = defaultdict(list)
    machine_seconds = defaultdict(float)
    start_errors, calendar_errors, duration_errors, assigned_to_group = [], [], [], []
    for load in OperationPlanResource.objects.using(db).select_related(
        "operationplan__operation", "resource").order_by("resource_id", "operationplan__startdate"):
        op, machine = load.operationplan, load.resource
        active = active_segments(op)
        busy = seconds(active)
        calendar_busy = seconds(calendar_segments(machine.name, op.startdate, op.enddate))
        if op.startdate < START:
            start_errors.append(op.reference)
        if abs(busy - calendar_busy) > 2:
            calendar_errors.append({"reference": op.reference, "machine": machine.name,
                                    "active_seconds": busy, "calendar_seconds": calendar_busy})
        efficiency = float(machine.efficiency or 100) / 100
        expected = ((op.operation.duration or timedelta()).total_seconds()
                    + (op.operation.duration_per or timedelta()).total_seconds() * float(op.quantity)) / efficiency
        if abs(busy - expected) > 3:
            duration_errors.append({"reference": op.reference, "active_seconds": busy, "expected": expected})
        if machine.lvl != 2:
            assigned_to_group.append(machine.name)
        pegging = (op.plan or {}).get("pegging", {})
        orders = sorted(pegging)
        ship_tags = sorted({r["category"] for r in demand_rows if r["name"] in pegging})
        machine_rows.append({
            "machine": machine.name, "operation_reference": op.reference, "manufacturing_order": op.owner_id,
            "operation": op.operation_id, "description": op.operation.description,
            "shipments": ", ".join(ship_tags), "sales_orders": ", ".join(orders),
            "quantity": op.quantity, "start_local": op.startdate, "end_local": op.enddate,
            "active_machine_hours": round(busy / 3600, 4),
            "elapsed_hours_including_breaks": round((op.enddate - op.startdate).total_seconds() / 3600, 4),
            "status": op.status,
        })
        machine_seconds[machine.name] += busy
        for left, right in active:
            if right <= left:
                continue
            events[machine.name].extend(((left, float(load.quantity), op.reference), (right, -float(load.quantity), op.reference)))
            segments.append({"machine": machine.name, "operation_reference": op.reference,
                             "shipments": ", ".join(ship_tags), "active_start": left, "active_end": right,
                             "hours": round((right - left).total_seconds() / 3600, 4)})

    overlap_errors = []
    for machine, timeline in events.items():
        # At an exact boundary, an ending job releases capacity before the next starts.
        current = 0
        for at, change, reference in sorted(timeline, key=lambda row: (row[0], row[1])):
            current += change
            if current > 1.000001:
                overlap_errors.append({"machine": machine, "at": at, "load": current, "reference": reference})

    # The saved routing priority establishes a finish-to-start chain.
    precedence_errors = rows(db, """
        with steps as (
          select op.reference,op.owner_id,op.startdate,o.priority,
                 lag(op.enddate) over(partition by op.owner_id order by o.priority) as predecessor_end
          from operationplan op join operation o on o.name=op.operation_id
          where op.type='WO'
        ) select * from steps where predecessor_end > startdate + interval '1 second'
    """)
    # Sum simultaneous flows before testing balances; avoids false shortages at handoff times.
    material_shortages = rows(db, """
        with changes as (
          select item_id,location_id,flowdate,sum(quantity) as change
          from operationplanmaterial group by item_id,location_id,flowdate
        ), ledger as (
          select *,sum(change) over(partition by item_id,location_id order by flowdate) as balance
          from changes
        ) select * from ledger where balance < -0.00001
    """)
    purchase_errors = rows(db, """
        select op.reference,op.startdate,op.enddate,s.leadtime
        from operationplan op join itemsupplier s
          on s.item_id=op.item_id and s.location_id=op.location_id and s.supplier_id=op.supplier_id
        where op.type='PO' and op.status='proposed'
          and (op.startdate < %s or op.enddate-op.startdate < s.leadtime - interval '1 second')
    """, [START])
    ship_rows = []
    for tag, orders in sorted(shipments.items()):
        dates = [o["deliverydate"] for o in orders if o["deliverydate"]]
        starts = [r["start_local"] for r in machine_rows if tag in r["shipments"].split(", ")]
        ready = max(dates) if len(dates) == len(orders) else None
        ship_rows.append({"shipment": tag, "description": names.get(tag, {}).get("desc", tag),
                          "quantity": sum(o["quantity"] for o in orders), "order_lines": len(orders),
                          "products": "; ".join(f"{o['item_id']} x {o['quantity']:g}" for o in orders),
                          "due_local": max(o["due"] for o in orders), "planned_dispatch_local": ready,
                          "first_production_start": min(starts) if starts else None,
                          "on_time_full": all(o["on_time_full"] for o in orders),
                          "all_together": len(dates) == len(orders) and len(set(dates)) == 1})
    capacity = []
    for machine in Resource.objects.using(db).filter(source=SOURCE, lvl=2).order_by("name"):
        available = seconds(calendar_segments(machine.name, START, horizon)) / 3600
        load = machine_seconds[machine.name] / 3600
        capacity.append({"machine": machine.name, "description": machine.description,
                         "horizon_start": START, "horizon_end": horizon,
                         "staffed_available_hours": round(available, 3), "loaded_hours": round(load, 3),
                         "utilization_percent": round(100 * load / available, 2) if available else 0,
                         "efficiency_percent": machine.efficiency})
    original_dates = {f"CRI-SO-{i:03d}-{j:02d}": datetime.fromisoformat(date + " 17:00:00")
                      for i, (_, date, lines) in enumerate(SHIPMENTS, 1) for j, _ in enumerate(lines, 1)}
    latest = Task.objects.using(db).filter(name="runplan").order_by("-id").values(
        "id", "status", "arguments", "started", "finished", "logfile").first()
    checks = {
        "plan_task_done": bool(latest and latest["status"] == "Done"),
        "plan_date_is_19_october_2026": Parameter.getValue("currentdate", db, "") == START.isoformat(sep=" "),
        "exactly_10_shipments_20_lines_670_pumps": len(ship_rows) == 10 and len(demand_rows) == 20 and sum(r["quantity"] for r in demand_rows) == 670,
        "due_dates_match_source_dataset": all(r["due"] == original_dates.get(r["name"]) for r in demand_rows),
        "all_order_quantities_planned": all(r["plannedquantity"] == r["quantity"] and r["deliverydate"] for r in demand_rows),
        "all_shipments_on_time": all(r["on_time_full"] for r in ship_rows),
        "all_shipment_lines_together": all(r["all_together"] for r in ship_rows),
        "production_not_before_plan_date": not start_errors,
        "only_finite_leaf_machine_assignments": bool(machine_rows) and not assigned_to_group,
        "no_machine_overlaps": not overlap_errors,
        "shifts_breaks_and_maintenance_respected": not calendar_errors,
        "cycle_setup_and_efficiency_respected": not duration_errors,
        "routing_precedence_respected": not precedence_errors,
        "material_balances_nonnegative": not material_shortages,
        "proposed_purchase_lead_times_respected": not purchase_errors,
    }
    result = {"dataset": SOURCE, "illustrative_inputs": True, "passed": all(checks.values()),
              "checks": checks, "latest_task": latest, "shipments": len(ship_rows),
              "on_time_shipments": sum(r["on_time_full"] for r in ship_rows),
              "total_pumps": sum(r["quantity"] for r in demand_rows),
              "scheduled_machine_operations": len(machine_rows),
              "active_machine_hours": round(sum(machine_seconds.values()) / 3600, 3),
              "first_production_start": min((r["start_local"] for r in machine_rows), default=None),
              "last_dispatch": last_delivery,
              "errors": {"overlaps": overlap_errors, "calendar": calendar_errors, "durations": duration_errors,
                         "precedence": precedence_errors, "materials": material_shortages, "purchases": purchase_errors}}
    (path / "validation.json").write_text(json.dumps(result, default=json_default, indent=2), encoding="utf-8")
    tables = {"Shipments": ship_rows, "Order lines": demand_rows, "Machine schedule": machine_rows,
              "Active shift segments": segments, "Machine capacity": capacity,
              "Purchase orders": list(OperationPlan.objects.using(db).filter(type="PO").order_by("enddate", "reference").values(
                  "reference", "item_id", "supplier_id", "quantity", "startdate", "enddate", "status")),
              "Material movements": rows(db, "select item_id,location_id,flowdate,operationplan_id,quantity,onhand from operationplanmaterial order by item_id,flowdate"),
              "Plan problems": list(Problem.objects.using(db).values()),
              "Solver constraints": list(Constraint.objects.using(db).values()),
              "Validation": [{"check": key, "passed": bool(value)} for key, value in checks.items()]}
    write_workbook(path / "CRI_Constrained_Schedule_2026-10-19.xlsx", tables)
    for name, data in (("shipments.csv", ship_rows), ("machine_schedule.csv", machine_rows),
                       ("active_shift_segments.csv", segments), ("machine_capacity.csv", capacity)):
        if data:
            with (path / name).open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(data[0]))
                writer.writeheader()
                writer.writerows({k: scalar(v) for k, v in row.items()} for row in data)
    return {k: v for k, v in result.items() if k not in ("errors", "latest_task")}
