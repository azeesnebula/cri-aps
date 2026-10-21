"""Lightweight order list. Only demand/item rows for one page are loaded.

No operation plans, dependency traces or constraint collections are fetched.
"""

from urllib.parse import quote, urlencode

from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Coalesce, NullIf
from django.http import Http404

from freppledb.input.models import Demand


def order_list_context(db, params, ship_names, prefix=""):
    base = Demand.objects.using(db).filter(status__in=("open", "quote")).annotate(
        shipment_tag=Coalesce(NullIf("category", Value("")), "name")
    )
    # Shipment options and counts come from demand, not the expensive cockpit.
    shipments = list(base.values("shipment_tag").annotate(
        orders=Count("name")
    ).order_by("shipment_tag"))
    for ship in shipments:
        ship["label"] = ship_names.get(ship["shipment_tag"], ship["shipment_tag"])
    shipments.sort(key=lambda ship: (ship["label"].casefold(), ship["shipment_tag"]))

    selected = params.get("dsq", "").strip()
    if selected and not any(s["shipment_tag"] == selected for s in shipments):
        raise Http404("no such shipment")
    query = params.get("q", "").strip()[:200]
    status = params.get("status", "all")
    if status not in ("all", "late", "ontime", "unplanned"):
        status = "all"
    try:
        per_page = int(params.get("per_page", 25))
    except (ValueError, TypeError):
        per_page = 25
    if per_page not in (25, 50, 100):
        per_page = 25

    scoped = base
    if selected:
        scoped = scoped.filter(shipment_tag=selected)
    if query:
        scoped = scoped.filter(
            Q(name__icontains=query) | Q(item__name__icontains=query)
            | Q(item__description__icontains=query)
        )
    late = Q(deliverydate__gt=F("due"))
    ontime = Q(deliverydate__lte=F("due"))
    unplanned = Q(deliverydate__isnull=True)
    counts = scoped.aggregate(
        total=Count("name"), late=Count("name", filter=late),
        ontime=Count("name", filter=ontime), unplanned=Count("name", filter=unplanned),
    )
    if status != "all":
        scoped = scoped.filter({"late": late, "ontime": ontime, "unplanned": unplanned}[status])

    # A stable order prevents duplicate/skipped rows as the user pages through.
    values = scoped.order_by("due", "name").values(
        "name", "item_id", "item__description", "shipment_tag",
        "quantity", "plannedquantity", "due", "deliverydate",
    )
    page = Paginator(values, per_page).get_page(params.get("page", 1))
    orders = list(page.object_list)
    for order in orders:
        finish, due = order["deliverydate"], order["due"]
        order["state"] = (
            "unplanned" if finish is None else "unknown" if due is None
            else "late" if finish > due else "ontime"
        )
        order["late_days"] = max(0, (finish - due).total_seconds() / 86400) if finish and due else None
        order["short"] = finish is not None and (
            order["plannedquantity"] is None or order["plannedquantity"] < order["quantity"]
        )
        order["shipment_name"] = ship_names.get(order["shipment_tag"], order["shipment_tag"])
        order["graph_url"] = prefix + "/build-graph/" + quote(order["name"], safe="") + "/"
        order["evidence_url"] = prefix + "/constraint/?" + urlencode({"demand": order["name"]})

    filters = {"status": status, "per_page": per_page}
    if selected:
        filters["dsq"] = selected
    if query:
        filters["q"] = query
    def page_url(number):
        return prefix + "/production-orders/?" + urlencode(dict(filters, page=number))

    return {
        "shipments": shipments, "selected_shipment": selected,
        "all_order_count": sum(ship["orders"] for ship in shipments),
        "delivery_status": status, "search": query, "per_page": per_page,
        "page_sizes": (25, 50, 100), "counts": counts, "page": page, "orders": orders,
        "previous_url": page_url(page.previous_page_number()) if page.has_previous() else None,
        "next_url": page_url(page.next_page_number()) if page.has_next() else None,
        "reset_url": prefix + "/production-orders/",
    }
