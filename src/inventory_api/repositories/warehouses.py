from __future__ import annotations

import sqlite3

from ..models import Location, Warehouse


class WarehouseRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_warehouse(self, code: str, name: str, now: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO warehouses (code, name, created_at) VALUES (?, ?, ?)", (code, name, now)
        )
        return int(cursor.lastrowid)

    def get_warehouse(self, warehouse_id: int) -> Warehouse | None:
        row = self.conn.execute("SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
        return Warehouse.from_row(row) if row else None

    def find_warehouse_code(self, code: str) -> Warehouse | None:
        row = self.conn.execute("SELECT * FROM warehouses WHERE code = ?", (code,)).fetchone()
        return Warehouse.from_row(row) if row else None

    def list_warehouses(self) -> list[Warehouse]:
        rows = self.conn.execute("SELECT * FROM warehouses ORDER BY id")
        return [Warehouse.from_row(row) for row in rows]

    def insert_location(self, warehouse_id: int, code: str, name: str, now: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO locations (warehouse_id, code, name, created_at) VALUES (?, ?, ?, ?)",
            (warehouse_id, code, name, now),
        )
        return int(cursor.lastrowid)

    def get_location(self, location_id: int) -> Location | None:
        row = self.conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
        return Location.from_row(row) if row else None

    def find_location_code(self, warehouse_id: int, code: str) -> Location | None:
        row = self.conn.execute(
            "SELECT * FROM locations WHERE warehouse_id = ? AND code = ?", (warehouse_id, code)
        ).fetchone()
        return Location.from_row(row) if row else None

    def list_locations(self, warehouse_id: int) -> list[Location]:
        rows = self.conn.execute(
            "SELECT * FROM locations WHERE warehouse_id = ? ORDER BY id", (warehouse_id,)
        )
        return [Location.from_row(row) for row in rows]

    def set_location_active(self, location_id: int, active: bool) -> None:
        self.conn.execute(
            "UPDATE locations SET active = ? WHERE id = ?", (1 if active else 0, location_id)
        )
