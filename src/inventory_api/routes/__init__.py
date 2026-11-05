"""HTTP handlers. They translate requests to service calls and results to JSON."""

from __future__ import annotations

from ..http.router import Router
from . import catalog, ops, purchasing, reservations, stock, warehouses


def register_all(router: Router) -> None:
    for module in (ops, catalog, stock, warehouses, reservations, purchasing):
        module.register(router)
