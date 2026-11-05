"""Health, reports and the audit trail."""

from __future__ import annotations

from ..http.request import Request
from ..http.response import Response, json_response
from ..http.router import Router
from ..migrations import MIGRATIONS, applied_versions
from ..pagination import page_from_request, paginate


def health(request: Request) -> Response:
    context = request.services.context
    applied = applied_versions(context.conn)
    latest = MIGRATIONS[-1].version
    current = max(applied, default=0)
    if current != latest:
        return json_response({"status": "needs_migration", "schema": current, "expected": latest}, 503)
    return json_response({"status": "ok", "schema": current})


def low_stock(request: Request) -> Response:
    rows = request.services.reports.low_stock(exclude_covered=request.query_bool("exclude_covered"))
    return json_response({"items": rows})


def valuation(request: Request) -> Response:
    warehouse_id = request.query_int("warehouse_id", None, minimum=1)
    if warehouse_id is not None:
        request.services.locations.require_warehouse(warehouse_id)
    return json_response(request.services.reports.valuation(warehouse_id))


def movement_totals(request: Request) -> Response:
    report = request.services.reports.movement_totals(
        request.query_time("since"), request.query_time("until")
    )
    return json_response(report)


def audit_log(request: Request) -> Response:
    page = page_from_request(request)
    rows = request.services.context.audit_log.list(
        limit=page.fetch_size,
        cursor=page.cursor,
        entity=request.first("entity"),
        entity_id=request.query_int("entity_id", None, minimum=0),
    )
    return json_response(paginate(rows, page, cursor_of=lambda e: e.id, render=lambda e: e.to_json()))


def register(router: Router) -> None:
    router.get("/health", health)
    router.get("/reports/low-stock", low_stock)
    router.get("/reports/valuation", valuation)
    router.get("/reports/movements", movement_totals)
    router.get("/audit", audit_log)
