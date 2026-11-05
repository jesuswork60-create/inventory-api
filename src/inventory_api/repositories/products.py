from __future__ import annotations

import sqlite3
from typing import Any

from ..models import Product

_UPDATABLE = {"sku", "name", "description", "unit_price_cents", "reorder_point"}


class ProductRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, values: dict[str, Any], now: str) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO products
                (sku, name, description, unit_price_cents, reorder_point, created_at, updated_at)
            VALUES (:sku, :name, :description, :unit_price_cents, :reorder_point, :now, :now)
            """,
            {"description": "", "reorder_point": 0, **values, "now": now},
        )
        return int(cursor.lastrowid)

    def get(self, product_id: int, *, include_deleted: bool = False) -> Product | None:
        sql = "SELECT * FROM products WHERE id = ?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        row = self.conn.execute(sql, (product_id,)).fetchone()
        return Product.from_row(row) if row else None

    def find_active_sku(self, sku: str) -> Product | None:
        row = self.conn.execute(
            "SELECT * FROM products WHERE sku = ? COLLATE NOCASE AND deleted_at IS NULL", (sku,)
        ).fetchone()
        return Product.from_row(row) if row else None

    def list(
        self,
        *,
        limit: int,
        cursor: int | None = None,
        search: str | None = None,
        include_deleted: bool = False,
    ) -> list[Product]:
        clauses = ["id > ?"]
        args: list[Any] = [cursor or 0]
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if search:
            pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            clauses.append("(sku LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\')")
            args += [pattern, pattern]
        args.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM products WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?", args
        )
        return [Product.from_row(row) for row in rows]

    def update(self, product_id: int, changes: dict[str, Any], now: str) -> None:
        unknown = changes.keys() - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        assignments = "".join(f"{name} = :{name}, " for name in changes)
        self.conn.execute(
            f"""
            UPDATE products
            SET {assignments}version = version + 1, updated_at = :now
            WHERE id = :id
            """,
            {**changes, "now": now, "id": product_id},
        )

    def mark_deleted(self, product_id: int, now: str) -> None:
        self.conn.execute(
            "UPDATE products SET deleted_at = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (now, now, product_id),
        )

    def set_average_cost(self, product_id: int, cents: int) -> None:
        # Cost changes come from stock arriving, not from editing the product, so
        # they do not bump the version a client may be holding for its edit.
        self.conn.execute("UPDATE products SET avg_cost_cents = ? WHERE id = ?", (cents, product_id))
