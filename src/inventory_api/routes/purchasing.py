from __future__ import annotations

from ..http.request import Request
from ..http.response import Response, json_response
from ..http.router import Router
from ..pagination import page_from_request, paginate
from .common import created, idempotent


def list_orders(request: Request) -> Response:
    page = page_from_request(request)
    rows = request.services.purchasing.list(
        limit=page.fetch_size, cursor=page.cursor, status=request.first("status")
    )
    return json_response(paginate(rows, page, cursor_of=lambda o: o.id, render=lambda o: o.to_json()))


@idempotent
def create_order(request: Request) -> Response:
    order = request.services.purchasing.create(request.json())
    return created(order.to_json(), f"/purchase-orders/{order.id}")


def get_order(request: Request) -> Response:
    return json_response(request.services.purchasing.require(request.params["id"]).to_json())


def submit_order(request: Request) -> Response:
    return json_response(request.services.purchasing.submit(request.params["id"]).to_json())


def cancel_order(request: Request) -> Response:
    return json_response(request.services.purchasing.cancel(request.params["id"]).to_json())


def close_order(request: Request) -> Response:
    return json_response(request.services.purchasing.close(request.params["id"]).to_json())


@idempotent
def receive_order(request: Request) -> Response:
    order = request.services.purchasing.receive(request.params["id"], request.json())
    return json_response(order.to_json())


def register(router: Router) -> None:
    router.get("/purchase-orders", list_orders)
    router.post("/purchase-orders", create_order)
    router.get("/purchase-orders/{id:int}", get_order)
    router.post("/purchase-orders/{id:int}/submit", submit_order)
    router.post("/purchase-orders/{id:int}/cancel", cancel_order)
    router.post("/purchase-orders/{id:int}/close", close_order)
    router.post("/purchase-orders/{id:int}/receive", receive_order)
