"""Warehouses and the locations inside them."""

from __future__ import annotations

from typing import Any

from ..db import transaction
from ..errors import Conflict, NotFound
from ..models import Location, Warehouse
from ..validation import Schema, Text
from .context import ServiceContext

_CODE = r"[A-Za-z0-9][A-Za-z0-9._-]*"

WAREHOUSE_SCHEMA = Schema(
    {
        "code": Text(required=True, min_length=1, max_length=32, pattern=_CODE),
        "name": Text(required=True, min_length=1, max_length=200),
    }
)
LOCATION_SCHEMA = Schema(
    {
        "code": Text(required=True, min_length=1, max_length=32, pattern=_CODE),
        "name": Text(max_length=200),
    }
)


class LocationService:
    def __init__(self, context: ServiceContext) -> None:
        self.context = context

    def create_warehouse(self, body: Any) -> Warehouse:
        values = WAREHOUSE_SCHEMA.load(body)
        ctx = self.context
        with transaction(ctx.conn):
            if ctx.warehouses.find_warehouse_code(values["code"]):
                raise Conflict(f"warehouse {values['code']} already exists", code="duplicate_code")
            warehouse_id = ctx.warehouses.insert_warehouse(values["code"], values["name"], ctx.now())
            ctx.audit("warehouse", warehouse_id, "created", code=values["code"])
        return self.require_warehouse(warehouse_id)

    def require_warehouse(self, warehouse_id: int) -> Warehouse:
        warehouse = self.context.warehouses.get_warehouse(warehouse_id)
        if warehouse is None:
            raise NotFound(f"no warehouse {warehouse_id}")
        return warehouse

    def list_warehouses(self) -> list[Warehouse]:
        return self.context.warehouses.list_warehouses()

    def create_location(self, warehouse_id: int, body: Any) -> Location:
        values = LOCATION_SCHEMA.load(body)
        ctx = self.context
        with transaction(ctx.conn):
            self.require_warehouse(warehouse_id)
            if ctx.warehouses.find_location_code(warehouse_id, values["code"]):
                raise Conflict(f"location {values['code']} already exists", code="duplicate_code")
            location_id = ctx.warehouses.insert_location(
                warehouse_id, values["code"], values.get("name", ""), ctx.now()
            )
            ctx.audit("location", location_id, "created", code=values["code"])
        return self.require_location(location_id)

    def require_location(self, location_id: int) -> Location:
        location = self.context.warehouses.get_location(location_id)
        if location is None:
            raise NotFound(f"no location {location_id}")
        return location

    def require_active_location(self, location_id: int) -> Location:
        location = self.require_location(location_id)
        if not location.active:
            raise Conflict(f"location {location_id} is not active", code="location_inactive")
        return location

    def list_locations(self, warehouse_id: int) -> list[Location]:
        self.require_warehouse(warehouse_id)
        return self.context.warehouses.list_locations(warehouse_id)

    def deactivate_location(self, location_id: int) -> Location:
        ctx = self.context
        with transaction(ctx.conn):
            self.require_location(location_id)
            remaining = ctx.stock.location_total(location_id)
            if remaining:
                raise Conflict(
                    f"location still holds {remaining} unit(s)",
                    code="location_not_empty",
                    details={"on_hand": remaining},
                )
            ctx.warehouses.set_location_active(location_id, False)
            ctx.audit("location", location_id, "deactivated")
        return self.require_location(location_id)

    def activate_location(self, location_id: int) -> Location:
        ctx = self.context
        with transaction(ctx.conn):
            self.require_location(location_id)
            ctx.warehouses.set_location_active(location_id, True)
            ctx.audit("location", location_id, "activated")
        return self.require_location(location_id)
