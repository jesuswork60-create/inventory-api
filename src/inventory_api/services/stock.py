"""The stock ledger: every change to a level is a movement row in the same transaction."""

from __future__ import annotations

from typing import Any

from ..db import transaction
from ..errors import BadRequest, Conflict, NotFound
from ..models import Movement, Transfer
from ..validation import Integer, Schema, Text
from .catalog import CatalogService
from .context import ServiceContext
from .locations import LocationService

DEFAULT_LOCATION_ID = 1

# Kinds a client may post directly, and the sign they apply to stock.
INBOUND = ("receipt", "return")
OUTBOUND = ("shipment", "scrap")
CLIENT_KINDS = INBOUND + OUTBOUND + ("adjustment",)
SYSTEM_KINDS = ("transfer_out", "transfer_in", "fulfillment", "po_receipt")
ALL_KINDS = CLIENT_KINDS + SYSTEM_KINDS

_REFERENCE = Text(max_length=100)
_NOTE = Text(max_length=500)

MOVEMENT_SCHEMA = Schema(
    {
        "kind": Text(required=True, choices=CLIENT_KINDS),
        "quantity": Integer(required=True, minimum=-(10**9), maximum=10**9, nonzero=True),
        "location_id": Integer(minimum=1),
        "unit_cost_cents": Integer(minimum=0, maximum=10**12),
        "reference": _REFERENCE,
        "note": _NOTE,
    }
)
TRANSFER_SCHEMA = Schema(
    {
        "product_id": Integer(required=True, minimum=1),
        "from_location_id": Integer(required=True, minimum=1),
        "to_location_id": Integer(required=True, minimum=1),
        "quantity": Integer(required=True, minimum=1, maximum=10**9),
        "reference": _REFERENCE,
    }
)


def weighted_average(on_hand: int, current_cents: int, incoming: int, incoming_cents: int) -> int:
    """New average cost after stock arrives, rounded half up to a whole cent."""
    total_units = on_hand + incoming
    if total_units <= 0:
        return current_cents
    numerator = on_hand * current_cents + incoming * incoming_cents
    return (2 * numerator + total_units) // (2 * total_units)


class StockService:
    def __init__(self, context: ServiceContext) -> None:
        self.context = context
        self._catalog = CatalogService(context)
        self._locations = LocationService(context)

    def available(self, product_id: int, location_id: int) -> int:
        ctx = self.context
        return ctx.stock.on_hand(product_id, location_id) - ctx.reservations.reserved_quantity(
            product_id, location_id, ctx.now()
        )

    def require_available(self, product_id: int, location_id: int, quantity: int) -> None:
        available = self.available(product_id, location_id)
        if quantity > available:
            raise Conflict(
                f"only {max(available, 0)} unit(s) available at location {location_id}",
                code="insufficient_stock",
                details={"available": available, "requested": quantity},
            )

    def apply(
        self,
        product_id: int,
        location_id: int,
        kind: str,
        delta: int,
        *,
        unit_cost_cents: int | None = None,
        reference: str = "",
        note: str = "",
        transfer_id: int | None = None,
    ) -> Movement:
        """Move stock and write the ledger row. Callers hold the transaction."""
        ctx = self.context
        if delta > 0 and unit_cost_cents is not None:
            self._update_average_cost(product_id, delta, unit_cost_cents)
        ctx.stock.apply_delta(product_id, location_id, delta)
        return ctx.stock.insert_movement(
            product_id=product_id,
            location_id=location_id,
            kind=kind,
            quantity=delta,
            now=ctx.now(),
            unit_cost_cents=unit_cost_cents,
            reference=reference,
            note=note,
            transfer_id=transfer_id,
        )

    def _update_average_cost(self, product_id: int, incoming: int, unit_cost_cents: int) -> None:
        ctx = self.context
        product = ctx.products.get(product_id, include_deleted=True)
        on_hand = ctx.stock.total_on_hand(product_id)
        average = weighted_average(on_hand, product.avg_cost_cents, incoming, unit_cost_cents)
        ctx.products.set_average_cost(product_id, average)

    def record(self, product_id: int, body: Any) -> tuple[Movement, int, int]:
        """Handle a client-posted movement; returns it with new on-hand and available."""
        values = MOVEMENT_SCHEMA.load(body)
        kind = values["kind"]
        quantity = values["quantity"]
        location_id = values.get("location_id", DEFAULT_LOCATION_ID)
        reference = values.get("reference", "")
        note = values.get("note", "")
        unit_cost = values.get("unit_cost_cents")

        if kind != "adjustment" and quantity < 0:
            raise BadRequest(f"a {kind} takes a positive quantity")
        if unit_cost is not None and not (kind == "receipt" or (kind == "adjustment" and quantity > 0)):
            raise BadRequest("unit_cost_cents only applies to stock coming in")

        delta = -quantity if kind in OUTBOUND else quantity
        ctx = self.context
        with transaction(ctx.conn):
            self._catalog.require(product_id)
            if delta > 0:
                self._locations.require_active_location(location_id)
            else:
                self._locations.require_location(location_id)
            if kind in OUTBOUND:
                self.require_available(product_id, location_id, -delta)
            elif delta < 0:
                # A stock count may cut into reservations, since it reports what is
                # physically there, but it can never go below zero.
                on_hand = ctx.stock.on_hand(product_id, location_id)
                if -delta > on_hand:
                    raise Conflict(
                        f"only {on_hand} unit(s) on hand at location {location_id}",
                        code="insufficient_stock",
                        details={"on_hand": on_hand, "requested": -delta},
                    )
            movement = self.apply(
                product_id,
                location_id,
                kind,
                delta,
                unit_cost_cents=unit_cost,
                reference=reference,
                note=note,
            )
            ctx.audit("product", product_id, f"stock_{kind}", quantity=delta, location_id=location_id)
            return movement, ctx.stock.on_hand(product_id, location_id), self.available(product_id, location_id)

    def summary(self, product_id: int) -> dict[str, Any]:
        ctx = self.context
        product = self._catalog.require(product_id)
        locations = [
            {
                "location_id": row["location_id"],
                "location_code": row["location_code"],
                "warehouse_code": row["warehouse_code"],
                "on_hand": row["on_hand"],
                "reserved": row["reserved"],
                "available": row["on_hand"] - row["reserved"],
            }
            for row in ctx.stock.levels_for_product(product_id, ctx.now())
        ]
        return {
            "product_id": product.id,
            "sku": product.sku,
            "on_hand": sum(item["on_hand"] for item in locations),
            "reserved": sum(item["reserved"] for item in locations),
            "available": sum(item["available"] for item in locations),
            "locations": locations,
        }

    def movements(
        self,
        product_id: int,
        *,
        limit: int,
        cursor: int | None,
        kind: str | None,
        location_id: int | None,
        since: str | None,
        until: str | None,
    ) -> list[Movement]:
        ctx = self.context
        if ctx.products.get(product_id, include_deleted=True) is None:
            raise NotFound(f"no product {product_id}")
        if kind is not None and kind not in ALL_KINDS:
            raise BadRequest(f"unknown movement kind {kind}")
        return ctx.stock.list_movements(
            product_id,
            limit=limit,
            cursor=cursor,
            kind=kind,
            location_id=location_id,
            since=since,
            until=until,
        )

    def transfer(self, body: Any) -> Transfer:
        """Move units between locations as one atomic pair of ledger rows.

        Average cost is per product across all locations, so a transfer leaves it
        unchanged.
        """
        values = TRANSFER_SCHEMA.load(body)
        product_id = values["product_id"]
        source = values["from_location_id"]
        target = values["to_location_id"]
        quantity = values["quantity"]
        reference = values.get("reference", "")
        if source == target:
            raise BadRequest("from_location_id and to_location_id must differ")
        ctx = self.context
        with transaction(ctx.conn):
            self._catalog.require(product_id)
            self._locations.require_location(source)
            self._locations.require_active_location(target)
            self.require_available(product_id, source, quantity)
            record = ctx.stock.insert_transfer(product_id, source, target, quantity, reference, ctx.now())
            self.apply(product_id, source, "transfer_out", -quantity, reference=reference, transfer_id=record.id)
            self.apply(product_id, target, "transfer_in", quantity, reference=reference, transfer_id=record.id)
            ctx.audit("transfer", record.id, "created", product_id=product_id, quantity=quantity)
            return record

    def require_transfer(self, transfer_id: int) -> Transfer:
        record = self.context.stock.get_transfer(transfer_id)
        if record is None:
            raise NotFound(f"no transfer {transfer_id}")
        return record
