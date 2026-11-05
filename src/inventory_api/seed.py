"""A little made-up data so there is something to look at after a fresh install."""

from __future__ import annotations

import sqlite3

from .clock import Clock, system_clock
from .db import transaction
from .services import Services

_PRODUCTS = (
    ("WID-RED", "Red widget", 1299, 10, 40, 450),
    ("WID-BLU", "Blue widget", 1399, 10, 6, 500),
    ("GIZ-100", "Small gizmo", 2500, 5, 3, 1200),
    ("GIZ-200", "Large gizmo", 4500, 0, 12, 2100),
)


def seed(conn: sqlite3.Connection, *, clock: Clock = system_clock) -> int:
    """Insert the sample products if the catalogue is empty. Returns how many."""
    if conn.execute("SELECT 1 FROM products LIMIT 1").fetchone():
        return 0
    services = Services(conn, clock, "seed")
    with transaction(conn):
        for sku, name, price, reorder, stock, cost in _PRODUCTS:
            product = services.catalog.create(
                {"sku": sku, "name": name, "unit_price_cents": price, "reorder_point": reorder}
            )
            services.stock.record(
                product.id,
                {"kind": "receipt", "quantity": stock, "unit_cost_cents": cost, "reference": "seed"},
            )
    return len(_PRODUCTS)
