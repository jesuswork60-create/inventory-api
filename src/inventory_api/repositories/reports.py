"""Read-only aggregate queries."""

from __future__ import annotations

import sqlite3


class ReportRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def stock_positions(self, now: str) -> list[sqlite3.Row]:
        """For each live product: on hand, held by reservations, and still on order."""
        return self.conn.execute(
            """
            SELECT p.id, p.sku, p.name, p.reorder_point,
                   COALESCE((SELECT SUM(on_hand) FROM stock_levels s WHERE s.product_id = p.id), 0)
                       AS on_hand,
                   COALESCE((SELECT SUM(quantity - fulfilled_quantity) FROM reservations r
                             WHERE r.product_id = p.id AND r.status = 'active'
                               AND (r.expires_at IS NULL OR r.expires_at > :now)), 0)
                       AS reserved,
                   COALESCE((SELECT SUM(l.quantity_ordered - l.quantity_received)
                             FROM purchase_order_lines l
                             JOIN purchase_orders o ON o.id = l.order_id
                             WHERE l.product_id = p.id
                               AND o.status IN ('ordered', 'partially_received')), 0)
                       AS incoming
            FROM products p
            WHERE p.deleted_at IS NULL
            ORDER BY p.sku
            """,
            {"now": now},
        ).fetchall()

    def valuation(self, warehouse_id: int | None = None) -> list[sqlite3.Row]:
        scope = ""
        args: list[int] = []
        if warehouse_id is not None:
            scope = "AND l.warehouse_id = ?"
            args.append(warehouse_id)
        return self.conn.execute(
            f"""
            SELECT p.id, p.sku, p.name, p.avg_cost_cents, SUM(s.on_hand) AS on_hand,
                   SUM(s.on_hand) * p.avg_cost_cents AS value_cents
            FROM stock_levels s
            JOIN products p ON p.id = s.product_id
            JOIN locations l ON l.id = s.location_id
            WHERE s.on_hand > 0 {scope}
            GROUP BY p.id
            ORDER BY value_cents DESC, p.sku
            """,
            args,
        ).fetchall()

    def movement_totals(self, since: str | None, until: str | None) -> list[sqlite3.Row]:
        clauses = ["1 = 1"]
        args: list[str] = []
        if since:
            clauses.append("created_at >= ?")
            args.append(since)
        if until:
            clauses.append("created_at < ?")
            args.append(until)
        return self.conn.execute(
            f"""
            SELECT kind, COUNT(*) AS movements, SUM(quantity) AS net_quantity,
                   COUNT(DISTINCT product_id) AS products
            FROM movements
            WHERE {' AND '.join(clauses)}
            GROUP BY kind
            ORDER BY kind
            """,
            args,
        ).fetchall()
