"""Row types. Each knows how to read itself from a database row and print itself."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field, fields
from typing import Any, TypeVar

T = TypeVar("T", bound="Model")


class Model:
    @classmethod
    def from_row(cls: type[T], row: sqlite3.Row) -> T:
        available = row.keys()
        return cls(**{f.name: row[f.name] for f in fields(cls) if f.name in available})  # type: ignore[arg-type]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]


@dataclass(slots=True)
class Product(Model):
    id: int
    sku: str
    name: str
    description: str
    unit_price_cents: int
    reorder_point: int
    avg_cost_cents: int
    version: int
    deleted_at: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class Warehouse(Model):
    id: int
    code: str
    name: str
    created_at: str


@dataclass(slots=True)
class Location(Model):
    id: int
    warehouse_id: int
    code: str
    name: str
    active: int
    created_at: str

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["active"] = bool(self.active)
        return data


@dataclass(slots=True)
class Movement(Model):
    id: int
    product_id: int
    location_id: int
    kind: str
    quantity: int
    unit_cost_cents: int | None
    reference: str
    note: str
    transfer_id: int | None
    created_at: str


@dataclass(slots=True)
class Transfer(Model):
    id: int
    product_id: int
    from_location_id: int
    to_location_id: int
    quantity: int
    reference: str
    created_at: str


@dataclass(slots=True)
class Reservation(Model):
    id: int
    product_id: int
    location_id: int
    quantity: int
    fulfilled_quantity: int
    status: str
    reference: str
    expires_at: str | None
    created_at: str
    updated_at: str

    @property
    def open_quantity(self) -> int:
        return self.quantity - self.fulfilled_quantity

    def is_expired(self, now: str) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["open_quantity"] = self.open_quantity
        return data


@dataclass(slots=True)
class PurchaseOrderLine(Model):
    id: int
    order_id: int
    product_id: int
    quantity_ordered: int
    quantity_received: int
    unit_cost_cents: int

    @property
    def outstanding(self) -> int:
        return self.quantity_ordered - self.quantity_received

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["outstanding"] = self.outstanding
        return data


@dataclass(slots=True)
class PurchaseOrder(Model):
    id: int
    supplier: str
    status: str
    note: str
    created_at: str
    updated_at: str
    lines: list[PurchaseOrderLine] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["lines"] = [line.to_json() for line in self.lines]
        return data


@dataclass(slots=True)
class AuditEntry(Model):
    id: int
    entity: str
    entity_id: int
    action: str
    detail: str
    request_id: str | None
    created_at: str

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["detail"] = json.loads(self.detail)
        return data
