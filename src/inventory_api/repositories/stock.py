from __future__ import annotations

import sqlite3
from typing import Any

from ..models import Movement, Transfer


class StockRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def on_hand(self, product_id: int, location_id: int) -> int:
        row = self.conn.execute(
            "SELECT on_hand FROM stock_levels WHERE product_id = ? AND location_id = ?",
            (product_id, location_id),
        ).fetchone()
        return row["on_hand"] if row else 0

    def total_on_hand(self, product_id: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(on_hand), 0) AS total FROM stock_levels WHERE product_id = ?",
            (product_id,),
        ).fetchone()
        return int(row["total"])

    def location_total(self, location_id: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(on_hand), 0) AS total FROM stock_levels WHERE location_id = ?",
            (location_id,),
        ).fetchone()
        return int(row["total"])

    def apply_delta(self, product_id: int, location_id: int, delta: int) -> int:
        """Change the level and return it. The table's CHECK refuses negatives.

        This is an update first rather than an upsert because SQLite checks the
        proposed row, negative delta included, before it considers the conflict.
        """
        updated = self.conn.execute(
            "UPDATE stock_levels SET on_hand = on_hand + ? WHERE product_id = ? AND location_id = ?",
            (delta, product_id, location_id),
        )
        if updated.rowcount == 0:
            self.conn.execute(
                "INSERT INTO stock_levels (product_id, location_id, on_hand) VALUES (?, ?, ?)",
                (product_id, location_id, delta),
            )
        return self.on_hand(product_id, location_id)

    def levels_for_product(self, product_id: int, now: str) -> list[sqlite3.Row]:
        """Every location with stock or an unexpired reservation for this product."""
        return self.conn.execute(
            """
            WITH reserved AS (
                SELECT location_id, SUM(quantity - fulfilled_quantity) AS quantity
                FROM reservations
                WHERE product_id = :product AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > :now)
                GROUP BY location_id
            ),
            known AS (
                SELECT location_id FROM stock_levels WHERE product_id = :product
                UNION
                SELECT location_id FROM reserved
            )
            SELECT l.id AS location_id, l.code AS location_code, w.code AS warehouse_code,
                   COALESCE(s.on_hand, 0) AS on_hand, COALESCE(r.quantity, 0) AS reserved
            FROM known k
            JOIN locations l ON l.id = k.location_id
            JOIN warehouses w ON w.id = l.warehouse_id
            LEFT JOIN stock_levels s ON s.location_id = k.location_id AND s.product_id = :product
            LEFT JOIN reserved r ON r.location_id = k.location_id
            ORDER BY l.id
            """,
            {"product": product_id, "now": now},
        ).fetchall()

    def insert_movement(
        self,
        *,
        product_id: int,
        location_id: int,
        kind: str,
        quantity: int,
        now: str,
        unit_cost_cents: int | None = None,
        reference: str = "",
        note: str = "",
        transfer_id: int | None = None,
    ) -> Movement:
        cursor = self.conn.execute(
            """
            INSERT INTO movements
                (product_id, location_id, kind, quantity, unit_cost_cents, reference, note,
                 transfer_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, location_id, kind, quantity, unit_cost_cents, reference, note, transfer_id, now),
        )
        row = self.conn.execute("SELECT * FROM movements WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return Movement.from_row(row)

    def list_movements(
        self,
        product_id: int,
        *,
        limit: int,
        cursor: int | None = None,
        kind: str | None = None,
        location_id: int | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[Movement]:
        """Newest first; the cursor is the id of the last row already seen."""
        clauses = ["product_id = ?"]
        args: list[Any] = [product_id]
        if cursor is not None:
            clauses.append("id < ?")
            args.append(cursor)
        if kind:
            clauses.append("kind = ?")
            args.append(kind)
        if location_id is not None:
            clauses.append("location_id = ?")
            args.append(location_id)
        if since:
            clauses.append("created_at >= ?")
            args.append(since)
        if until:
            clauses.append("created_at < ?")
            args.append(until)
        args.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM movements WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", args
        )
        return [Movement.from_row(row) for row in rows]

    def insert_transfer(
        self, product_id: int, source: int, target: int, quantity: int, reference: str, now: str
    ) -> Transfer:
        cursor = self.conn.execute(
            """
            INSERT INTO transfers
                (product_id, from_location_id, to_location_id, quantity, reference, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (product_id, source, target, quantity, reference, now),
        )
        row = self.conn.execute("SELECT * FROM transfers WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return Transfer.from_row(row)

    def get_transfer(self, transfer_id: int) -> Transfer | None:
        row = self.conn.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
        return Transfer.from_row(row) if row else None
