from __future__ import annotations

import sqlite3
from typing import Any

from ..models import Reservation

ACTIVE = "active"
FULFILLED = "fulfilled"
CANCELLED = "cancelled"
EXPIRED = "expired"
STATUSES = (ACTIVE, FULFILLED, CANCELLED, EXPIRED)


class ReservationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(
        self,
        product_id: int,
        location_id: int,
        quantity: int,
        reference: str,
        expires_at: str | None,
        now: str,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO reservations
                (product_id, location_id, quantity, status, reference, expires_at,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, location_id, quantity, ACTIVE, reference, expires_at, now, now),
        )
        return int(cursor.lastrowid)

    def get(self, reservation_id: int) -> Reservation | None:
        row = self.conn.execute(
            "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
        ).fetchone()
        return Reservation.from_row(row) if row else None

    def list(
        self,
        *,
        limit: int,
        cursor: int | None = None,
        status: str | None = None,
        product_id: int | None = None,
    ) -> list[Reservation]:
        clauses = ["id > ?"]
        args: list[Any] = [cursor or 0]
        if status:
            clauses.append("status = ?")
            args.append(status)
        if product_id is not None:
            clauses.append("product_id = ?")
            args.append(product_id)
        args.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM reservations WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?", args
        )
        return [Reservation.from_row(row) for row in rows]

    def reserved_quantity(self, product_id: int, location_id: int, now: str) -> int:
        """Units held at a location. An expired hold stops counting at once, even
        before the sweep has changed its status."""
        row = self.conn.execute(
            """
            SELECT COALESCE(SUM(quantity - fulfilled_quantity), 0) AS total
            FROM reservations
            WHERE product_id = ? AND location_id = ? AND status = 'active'
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (product_id, location_id, now),
        ).fetchone()
        return int(row["total"])

    def count_active(self, product_id: int, now: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total FROM reservations
            WHERE product_id = ? AND status = 'active'
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (product_id, now),
        ).fetchone()
        return int(row["total"])

    def add_fulfilled(self, reservation_id: int, quantity: int, status: str, now: str) -> None:
        self.conn.execute(
            """
            UPDATE reservations
            SET fulfilled_quantity = fulfilled_quantity + ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (quantity, status, now, reservation_id),
        )

    def set_status(self, reservation_id: int, status: str, now: str) -> None:
        self.conn.execute(
            "UPDATE reservations SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, reservation_id),
        )

    def overdue_ids(self, now: str, limit: int) -> list[int]:
        rows = self.conn.execute(
            """
            SELECT id FROM reservations
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
            ORDER BY id LIMIT ?
            """,
            (now, limit),
        )
        return [row["id"] for row in rows]
