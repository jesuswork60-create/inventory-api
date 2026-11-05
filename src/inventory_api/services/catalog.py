"""Products: create, read, change, retire."""

from __future__ import annotations

from typing import Any

from ..db import transaction
from ..errors import Conflict, NotFound, PreconditionFailed
from ..models import Product
from ..validation import Integer, Schema, Text
from .context import ServiceContext

_SKU_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_MONEY_MAX = 10**12

PRODUCT_SCHEMA = Schema(
    {
        "sku": Text(required=True, min_length=1, max_length=64, pattern=_SKU_PATTERN),
        "name": Text(required=True, min_length=1, max_length=200),
        "description": Text(max_length=2000),
        "unit_price_cents": Integer(required=True, minimum=0, maximum=_MONEY_MAX),
        "reorder_point": Integer(minimum=0),
    }
)


class CatalogService:
    def __init__(self, context: ServiceContext) -> None:
        self.context = context

    def require(self, product_id: int) -> Product:
        product = self.context.products.get(product_id)
        if product is None:
            raise NotFound(f"no product {product_id}")
        return product

    def create(self, body: Any) -> Product:
        values = PRODUCT_SCHEMA.load(body)
        ctx = self.context
        with transaction(ctx.conn):
            self._refuse_duplicate_sku(values["sku"])
            product_id = ctx.products.insert(values, ctx.now())
            ctx.audit("product", product_id, "created", sku=values["sku"])
        return self.require(product_id)

    def _refuse_duplicate_sku(self, sku: str, *, ignoring: int | None = None) -> None:
        existing = self.context.products.find_active_sku(sku)
        if existing is not None and existing.id != ignoring:
            raise Conflict(f"SKU {sku} is already in use", code="duplicate_sku")

    def list(
        self, *, limit: int, cursor: int | None, search: str | None, include_deleted: bool
    ) -> list[Product]:
        return self.context.products.list(
            limit=limit, cursor=cursor, search=search, include_deleted=include_deleted
        )

    def update(self, product_id: int, body: Any, expected_version: int | None) -> Product:
        """Apply a partial change. ``None`` for the version means any version will do."""
        changes = PRODUCT_SCHEMA.load(body, partial=True)
        ctx = self.context
        with transaction(ctx.conn):
            product = self.require(product_id)
            _check_version(product, expected_version)
            actual = {k: v for k, v in changes.items() if getattr(product, k) != v}
            if not actual:
                return product
            if "sku" in actual:
                self._refuse_duplicate_sku(actual["sku"], ignoring=product_id)
            ctx.products.update(product_id, actual, ctx.now())
            ctx.audit("product", product_id, "updated", changed=sorted(actual))
        return self.require(product_id)

    def delete(self, product_id: int, expected_version: int | None) -> None:
        """Retire a product. It stays in the database so its history stays readable.

        Open commitments block this: an active reservation or an open purchase
        order would otherwise point at something no longer on sale. Stock on hand
        does not, since retiring a line with some left over is ordinary.
        """
        ctx = self.context
        with transaction(ctx.conn):
            product = self.require(product_id)
            _check_version(product, expected_version)
            holds = ctx.reservations.count_active(product_id, ctx.now())
            if holds:
                raise Conflict(
                    f"product has {holds} active reservation(s)",
                    code="active_reservations",
                    details={"reservations": holds},
                )
            orders = ctx.purchases.open_order_count(product_id)
            if orders:
                raise Conflict(
                    f"product is on {orders} open purchase order(s)",
                    code="open_purchase_orders",
                    details={"purchase_orders": orders},
                )
            ctx.products.mark_deleted(product_id, ctx.now())
            ctx.audit("product", product_id, "deleted", sku=product.sku)


def _check_version(product: Product, expected: int | None) -> None:
    if expected is not None and expected != product.version:
        raise PreconditionFailed(
            f"product is at version {product.version}, not {expected}",
            details={"current_version": product.version},
        )
