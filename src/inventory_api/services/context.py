"""What every service shares for the length of one request."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from ..clock import Clock, to_stamp
from ..repositories.audit import AuditRepository, IdempotencyRepository
from ..repositories.products import ProductRepository
from ..repositories.purchasing import PurchaseRepository
from ..repositories.reports import ReportRepository
from ..repositories.reservations import ReservationRepository
from ..repositories.stock import StockRepository
from ..repositories.warehouses import WarehouseRepository


@dataclass
class ServiceContext:
    conn: sqlite3.Connection
    clock: Clock
    request_id: str | None = None

    def now(self) -> str:
        return to_stamp(self.clock())

    def audit(self, entity: str, entity_id: int, action: str, **detail: Any) -> None:
        self.audit_log.record(entity, entity_id, action, detail, self.request_id, self.now())

    @cached_property
    def products(self) -> ProductRepository:
        return ProductRepository(self.conn)

    @cached_property
    def warehouses(self) -> WarehouseRepository:
        return WarehouseRepository(self.conn)

    @cached_property
    def stock(self) -> StockRepository:
        return StockRepository(self.conn)

    @cached_property
    def reservations(self) -> ReservationRepository:
        return ReservationRepository(self.conn)

    @cached_property
    def purchases(self) -> PurchaseRepository:
        return PurchaseRepository(self.conn)

    @cached_property
    def audit_log(self) -> AuditRepository:
        return AuditRepository(self.conn)

    @cached_property
    def idempotency(self) -> IdempotencyRepository:
        return IdempotencyRepository(self.conn)

    @cached_property
    def report_queries(self) -> ReportRepository:
        return ReportRepository(self.conn)
