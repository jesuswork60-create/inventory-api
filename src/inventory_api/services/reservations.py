"""Holding stock for an order until it ships, is cancelled, or runs out of time."""

from __future__ import annotations

from typing import Any

from ..clock import add_seconds
from ..db import transaction
from ..errors import BadRequest, Conflict, NotFound
from ..models import Reservation
from ..repositories.reservations import ACTIVE, CANCELLED, EXPIRED, FULFILLED, STATUSES
from ..validation import Integer, Schema, Text
from .catalog import CatalogService
from .context import ServiceContext
from .locations import LocationService
from .stock import DEFAULT_LOCATION_ID, StockService

MAX_TTL_SECONDS = 90 * 24 * 3600
SWEEP_BATCH = 1000

RESERVATION_SCHEMA = Schema(
    {
        "product_id": Integer(required=True, minimum=1),
        "location_id": Integer(minimum=1),
        "quantity": Integer(required=True, minimum=1, maximum=10**9),
        "reference": Text(max_length=100),
        "ttl_seconds": Integer(minimum=1, maximum=MAX_TTL_SECONDS),
    }
)
FULFILL_SCHEMA = Schema({"quantity": Integer(minimum=1, maximum=10**9)})


class ReservationService:
    def __init__(self, context: ServiceContext) -> None:
        self.context = context
        self._catalog = CatalogService(context)
        self._locations = LocationService(context)
        self._stock = StockService(context)

    def create(self, body: Any) -> Reservation:
        values = RESERVATION_SCHEMA.load(body)
        product_id = values["product_id"]
        location_id = values.get("location_id", DEFAULT_LOCATION_ID)
        quantity = values["quantity"]
        ttl = values.get("ttl_seconds")
        ctx = self.context
        with transaction(ctx.conn):
            self._catalog.require(product_id)
            self._locations.require_active_location(location_id)
            self._stock.require_available(product_id, location_id, quantity)
            expires_at = add_seconds(ctx.clock(), ttl) if ttl is not None else None
            reservation_id = ctx.reservations.insert(
                product_id, location_id, quantity, values.get("reference", ""), expires_at, ctx.now()
            )
            ctx.audit("reservation", reservation_id, "created", product_id=product_id, quantity=quantity)
        return self.require(reservation_id)

    def require(self, reservation_id: int) -> Reservation:
        reservation = self.context.reservations.get(reservation_id)
        if reservation is None:
            raise NotFound(f"no reservation {reservation_id}")
        return reservation

    def list(
        self, *, limit: int, cursor: int | None, status: str | None, product_id: int | None
    ) -> list[Reservation]:
        if status is not None and status not in STATUSES:
            raise BadRequest(f"unknown reservation status {status}")
        return self.context.reservations.list(
            limit=limit, cursor=cursor, status=status, product_id=product_id
        )

    def _require_open(self, reservation_id: int) -> Reservation:
        reservation = self.require(reservation_id)
        if reservation.status != ACTIVE:
            raise Conflict(
                f"reservation is {reservation.status}",
                code="reservation_closed",
                details={"status": reservation.status},
            )
        if reservation.is_expired(self.context.now()):
            raise Conflict("reservation has expired", code="reservation_expired")
        return reservation

    def fulfill(self, reservation_id: int, body: Any) -> Reservation:
        """Ship against a hold. Leaving out the quantity ships all that remains.

        A partial shipment keeps the hold active for the rest. The stock level can
        be below the held amount after a count adjustment, and in that case the
        shipment is refused rather than letting the level go negative.
        """
        values = FULFILL_SCHEMA.load(body if body is not None else {})
        ctx = self.context
        with transaction(ctx.conn):
            reservation = self._require_open(reservation_id)
            quantity = values.get("quantity", reservation.open_quantity)
            if quantity > reservation.open_quantity:
                raise Conflict(
                    f"only {reservation.open_quantity} unit(s) remain on this reservation",
                    code="over_fulfillment",
                    details={"open_quantity": reservation.open_quantity},
                )
            on_hand = ctx.stock.on_hand(reservation.product_id, reservation.location_id)
            if quantity > on_hand:
                raise Conflict(
                    f"only {on_hand} unit(s) on hand at location {reservation.location_id}",
                    code="insufficient_stock",
                    details={"on_hand": on_hand, "requested": quantity},
                )
            self._stock.apply(
                reservation.product_id,
                reservation.location_id,
                "fulfillment",
                -quantity,
                reference=reservation.reference or f"reservation-{reservation.id}",
            )
            status = FULFILLED if quantity == reservation.open_quantity else ACTIVE
            ctx.reservations.add_fulfilled(reservation_id, quantity, status, ctx.now())
            ctx.audit("reservation", reservation_id, "fulfilled", quantity=quantity, status=status)
        return self.require(reservation_id)

    def cancel(self, reservation_id: int) -> Reservation:
        """Release a hold. Anything already shipped stays shipped."""
        ctx = self.context
        with transaction(ctx.conn):
            reservation = self.require(reservation_id)
            if reservation.status != ACTIVE:
                raise Conflict(
                    f"reservation is {reservation.status}",
                    code="reservation_closed",
                    details={"status": reservation.status},
                )
            ctx.reservations.set_status(reservation_id, CANCELLED, ctx.now())
            ctx.audit("reservation", reservation_id, "cancelled")
        return self.require(reservation_id)

    def expire_overdue(self) -> int:
        """Close holds past their time, in one bounded batch. Returns how many."""
        ctx = self.context
        with transaction(ctx.conn):
            now = ctx.now()
            overdue = ctx.reservations.overdue_ids(now, SWEEP_BATCH)
            for reservation_id in overdue:
                ctx.reservations.set_status(reservation_id, EXPIRED, now)
                ctx.audit("reservation", reservation_id, "expired")
        return len(overdue)
