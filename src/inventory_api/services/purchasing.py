"""Purchase orders: draft, ordered, received in as many deliveries as it takes."""

from __future__ import annotations

from typing import Any

from ..db import transaction
from ..errors import BadRequest, Conflict, NotFound
from ..models import PurchaseOrder
from ..repositories import purchasing as states
from ..validation import Integer, ListOf, Schema, Text
from .catalog import CatalogService
from .context import ServiceContext
from .locations import LocationService
from .stock import DEFAULT_LOCATION_ID, StockService

ORDER_SCHEMA = Schema(
    {
        "supplier": Text(required=True, min_length=1, max_length=200),
        "note": Text(max_length=500),
        "lines": ListOf(
            Schema(
                {
                    "product_id": Integer(required=True, minimum=1),
                    "quantity": Integer(required=True, minimum=1, maximum=10**9),
                    "unit_cost_cents": Integer(required=True, minimum=0, maximum=10**12),
                }
            ),
            required=True,
            min_items=1,
        ),
    }
)
RECEIVE_SCHEMA = Schema(
    {
        "location_id": Integer(minimum=1),
        "reference": Text(max_length=100),
        "lines": ListOf(
            Schema(
                {
                    "line_id": Integer(required=True, minimum=1),
                    "quantity": Integer(required=True, minimum=1, maximum=10**9),
                }
            ),
            required=True,
            min_items=1,
        ),
    }
)


class PurchasingService:
    def __init__(self, context: ServiceContext) -> None:
        self.context = context
        self._catalog = CatalogService(context)
        self._locations = LocationService(context)
        self._stock = StockService(context)

    def create(self, body: Any) -> PurchaseOrder:
        values = ORDER_SCHEMA.load(body)
        products = [line["product_id"] for line in values["lines"]]
        if len(set(products)) != len(products):
            raise BadRequest("each product may appear only once on an order")
        ctx = self.context
        with transaction(ctx.conn):
            order_id = ctx.purchases.insert_order(values["supplier"], values.get("note", ""), ctx.now())
            for line in values["lines"]:
                self._catalog.require(line["product_id"])
                ctx.purchases.insert_line(
                    order_id, line["product_id"], line["quantity"], line["unit_cost_cents"]
                )
            ctx.audit("purchase_order", order_id, "created", lines=len(products))
        return self.require(order_id)

    def require(self, order_id: int) -> PurchaseOrder:
        order = self.context.purchases.get(order_id)
        if order is None:
            raise NotFound(f"no purchase order {order_id}")
        return order

    def list(self, *, limit: int, cursor: int | None, status: str | None) -> list[PurchaseOrder]:
        if status is not None and status not in states.STATUSES:
            raise BadRequest(f"unknown purchase order status {status}")
        return self.context.purchases.list(limit=limit, cursor=cursor, status=status)

    def _transition(self, order_id: int, allowed: tuple[str, ...], status: str, action: str) -> PurchaseOrder:
        ctx = self.context
        with transaction(ctx.conn):
            order = self.require(order_id)
            if order.status not in allowed:
                raise Conflict(
                    f"cannot {action} an order that is {order.status}",
                    code="invalid_order_state",
                    details={"status": order.status},
                )
            ctx.purchases.set_status(order_id, status, ctx.now())
            ctx.audit("purchase_order", order_id, action)
        return self.require(order_id)

    def submit(self, order_id: int) -> PurchaseOrder:
        return self._transition(order_id, (states.DRAFT,), states.ORDERED, "submit")

    def cancel(self, order_id: int) -> PurchaseOrder:
        """Only an order with nothing received can be cancelled; see :meth:`close`."""
        return self._transition(order_id, (states.DRAFT, states.ORDERED), states.CANCELLED, "cancel")

    def close(self, order_id: int) -> PurchaseOrder:
        """Give up on the rest of a part-delivered order. What arrived stays."""
        return self._transition(order_id, (states.PARTIAL,), states.CLOSED, "close")

    def receive(self, order_id: int, body: Any) -> PurchaseOrder:
        """Book a delivery. All its lines land or none do."""
        values = RECEIVE_SCHEMA.load(body)
        location_id = values.get("location_id", DEFAULT_LOCATION_ID)
        ctx = self.context
        with transaction(ctx.conn):
            order = self.require(order_id)
            if order.status not in (states.ORDERED, states.PARTIAL):
                raise Conflict(
                    f"cannot receive against an order that is {order.status}",
                    code="invalid_order_state",
                    details={"status": order.status},
                )
            self._locations.require_active_location(location_id)
            lines = {line.id: line for line in order.lines}
            seen: set[int] = set()
            for delivery in values["lines"]:
                line_id = delivery["line_id"]
                if line_id in seen:
                    raise BadRequest(f"line {line_id} appears twice in this delivery")
                seen.add(line_id)
                line = lines.get(line_id)
                if line is None:
                    raise NotFound(f"order {order_id} has no line {line_id}")
                quantity = delivery["quantity"]
                if quantity > line.outstanding:
                    raise Conflict(
                        f"line {line_id} has only {line.outstanding} unit(s) outstanding",
                        code="over_receipt",
                        details={"line_id": line_id, "outstanding": line.outstanding},
                    )
                self._stock.apply(
                    line.product_id,
                    location_id,
                    "po_receipt",
                    quantity,
                    unit_cost_cents=line.unit_cost_cents,
                    reference=values.get("reference") or f"po-{order_id}",
                )
                ctx.purchases.add_received(line_id, quantity)
            refreshed = self.require(order_id)
            complete = all(line.outstanding == 0 for line in refreshed.lines)
            status = states.RECEIVED if complete else states.PARTIAL
            ctx.purchases.set_status(order_id, status, ctx.now())
            ctx.audit("purchase_order", order_id, "received", lines=len(seen), status=status)
        return self.require(order_id)
