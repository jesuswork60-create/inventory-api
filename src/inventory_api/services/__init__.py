"""Business rules. A :class:`Services` object is created per request."""

from __future__ import annotations

import sqlite3
from functools import cached_property

from ..clock import Clock
from .catalog import CatalogService
from .context import ServiceContext
from .locations import LocationService
from .purchasing import PurchasingService
from .reports import ReportService
from .reservations import ReservationService
from .stock import StockService


class Services:
    def __init__(self, conn: sqlite3.Connection, clock: Clock, request_id: str | None = None) -> None:
        self.context = ServiceContext(conn, clock, request_id)

    @cached_property
    def catalog(self) -> CatalogService:
        return CatalogService(self.context)

    @cached_property
    def locations(self) -> LocationService:
        return LocationService(self.context)

    @cached_property
    def stock(self) -> StockService:
        return StockService(self.context)

    @cached_property
    def reservations(self) -> ReservationService:
        return ReservationService(self.context)

    @cached_property
    def purchasing(self) -> PurchasingService:
        return PurchasingService(self.context)

    @cached_property
    def reports(self) -> ReportService:
        return ReportService(self.context)
