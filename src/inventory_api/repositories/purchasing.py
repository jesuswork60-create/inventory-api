from __future__ import annotations

import sqlite3
from typing import Any

from ..models import PurchaseOrder, PurchaseOrderLine

DRAFT = "draft"
ORDERED = "ordered"
PARTIAL = "partially_received"
RECEIVED = "received"
CLOSED = "closed"
CANCELLED = "cancelled"
STATUSES = (DRAFT, ORDERED, PARTIAL, RECEIVED, CLOSED, CANCELLED)


class PurchaseRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_order(self, supplier: str, note: str, now: str) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO purchase_orders (supplier, status, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (supplier, DRAFT, note, now, now),
        )
        return int(cursor.lastrowid)

    def insert_line(self, order_id: int, product_id: int, quantity: int, unit_cost_cents: int) -> None:
        self.conn.execute(
            """
            INSERT INTO purchase_order_lines (order_id, product_id, quantity_ordered, unit_cost_cents)
            VALUES (?, ?, ?, ?)
            """,
            (order_id, product_id, quantity, unit_cost_cents),
        )

    def get(self, order_id: int) -> PurchaseOrder | None:
        row = self.conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            return None
        order = PurchaseOrder.from_row(row)
        order.lines = self._lines_of(order_id)
        return order

    def _lines_of(self, order_id: int) -> list[PurchaseOrderLine]:
        rows = self.conn.execute(
            "SELECT * FROM purchase_order_lines WHERE order_id = ? ORDER BY id", (order_id,)
        )
        return [PurchaseOrderLine.from_row(row) for row in rows]

    def list(self, *, limit: int, cursor: int | None = None, status: str | None = None) -> list[PurchaseOrder]:
        clauses = ["id > ?"]
        args: list[Any] = [cursor or 0]
        if status:
            clauses.append("status = ?")
            args.append(status)
        args.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM purchase_orders WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?", args
        ).fetchall()
        orders = [PurchaseOrder.from_row(row) for row in rows]
        for order in orders:
            order.lines = self._lines_of(order.id)
        return orders

    def set_status(self, order_id: int, status: str, now: str) -> None:
        self.conn.execute(
            "UPDATE purchase_orders SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, order_id),
        )

    def add_received(self, line_id: int, quantity: int) -> None:
        self.conn.execute(
            "UPDATE purchase_order_lines SET quantity_received = quantity_received + ? WHERE id = ?",
            (quantity, line_id),
        )

    def open_order_count(self, product_id: int) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM purchase_order_lines l
            JOIN purchase_orders o ON o.id = l.order_id
            WHERE l.product_id = ? AND o.status IN ('draft', 'ordered', 'partially_received')
            """,
            (product_id,),
        ).fetchone()
        return int(row["total"])
