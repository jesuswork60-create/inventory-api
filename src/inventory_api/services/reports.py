"""Read-only views over stock."""

from __future__ import annotations

from typing import Any

from .context import ServiceContext


class ReportService:
    def __init__(self, context: ServiceContext) -> None:
        self.context = context

    def low_stock(self, *, exclude_covered: bool = False) -> list[dict[str, Any]]:
        """Products whose available quantity is at or below their reorder point.

        A reorder point of zero means the product is not tracked for reordering.
        With ``exclude_covered`` products that open purchase orders would bring back
        above the point are left out.
        """
        rows = []
        for row in self.context.report_queries.stock_positions(self.context.now()):
            if row["reorder_point"] <= 0:
                continue
            available = row["on_hand"] - row["reserved"]
            if available > row["reorder_point"]:
                continue
            if exclude_covered and available + row["incoming"] > row["reorder_point"]:
                continue
            rows.append(
                {
                    "product_id": row["id"],
                    "sku": row["sku"],
                    "name": row["name"],
                    "reorder_point": row["reorder_point"],
                    "on_hand": row["on_hand"],
                    "reserved": row["reserved"],
                    "available": available,
                    "incoming": row["incoming"],
                    "shortfall": row["reorder_point"] - available,
                }
            )
        rows.sort(key=lambda item: (-item["shortfall"], item["sku"]))
        return rows

    def valuation(self, warehouse_id: int | None = None) -> dict[str, Any]:
        items = [
            {
                "product_id": row["id"],
                "sku": row["sku"],
                "name": row["name"],
                "on_hand": row["on_hand"],
                "avg_cost_cents": row["avg_cost_cents"],
                "value_cents": row["value_cents"],
            }
            for row in self.context.report_queries.valuation(warehouse_id)
        ]
        return {
            "warehouse_id": warehouse_id,
            "total_value_cents": sum(item["value_cents"] for item in items),
            "items": items,
        }

    def movement_totals(self, since: str | None, until: str | None) -> dict[str, Any]:
        return {
            "since": since,
            "until": until,
            "kinds": [
                {
                    "kind": row["kind"],
                    "movements": row["movements"],
                    "net_quantity": row["net_quantity"],
                    "products": row["products"],
                }
                for row in self.context.report_queries.movement_totals(since, until)
            ],
        }
