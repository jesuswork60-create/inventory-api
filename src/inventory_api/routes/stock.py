from __future__ import annotations

from ..http.request import Request
from ..http.response import Response, json_response
from ..http.router import Router
from ..pagination import page_from_request, paginate
from .common import created, idempotent


def stock_summary(request: Request) -> Response:
    return json_response(request.services.stock.summary(request.params["id"]))


def list_movements(request: Request) -> Response:
    page = page_from_request(request)
    rows = request.services.stock.movements(
        request.params["id"],
        limit=page.fetch_size,
        cursor=page.cursor,
        kind=request.first("kind"),
        location_id=request.query_int("location_id", None, minimum=1),
        since=request.query_time("since"),
        until=request.query_time("until"),
    )
    return json_response(paginate(rows, page, cursor_of=lambda m: m.id, render=lambda m: m.to_json()))


@idempotent
def post_movement(request: Request) -> Response:
    movement, on_hand, available = request.services.stock.record(request.params["id"], request.json())
    return json_response(
        {"movement": movement.to_json(), "on_hand": on_hand, "available": available}, 201
    )


@idempotent
def post_transfer(request: Request) -> Response:
    record = request.services.stock.transfer(request.json())
    return created(record.to_json(), f"/transfers/{record.id}")


def get_transfer(request: Request) -> Response:
    return json_response(request.services.stock.require_transfer(request.params["id"]).to_json())


def register(router: Router) -> None:
    router.get("/products/{id:int}/stock", stock_summary)
    router.get("/products/{id:int}/movements", list_movements)
    router.post("/products/{id:int}/movements", post_movement)
    router.post("/transfers", post_transfer)
    router.get("/transfers/{id:int}", get_transfer)
