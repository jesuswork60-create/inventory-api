from __future__ import annotations

from ..http.request import Request
from ..http.response import Response, json_response
from ..http.router import Router
from ..pagination import page_from_request, paginate
from .common import created, idempotent, optional_json


@idempotent
def create_reservation(request: Request) -> Response:
    reservation = request.services.reservations.create(request.json())
    return created(reservation.to_json(), f"/reservations/{reservation.id}")


def list_reservations(request: Request) -> Response:
    page = page_from_request(request)
    rows = request.services.reservations.list(
        limit=page.fetch_size,
        cursor=page.cursor,
        status=request.first("status"),
        product_id=request.query_int("product_id", None, minimum=1),
    )
    return json_response(paginate(rows, page, cursor_of=lambda r: r.id, render=lambda r: r.to_json()))


def get_reservation(request: Request) -> Response:
    return json_response(request.services.reservations.require(request.params["id"]).to_json())


@idempotent
def fulfill_reservation(request: Request) -> Response:
    reservation = request.services.reservations.fulfill(request.params["id"], optional_json(request))
    return json_response(reservation.to_json())


def cancel_reservation(request: Request) -> Response:
    return json_response(request.services.reservations.cancel(request.params["id"]).to_json())


def expire_reservations(request: Request) -> Response:
    return json_response({"expired": request.services.reservations.expire_overdue()})


def register(router: Router) -> None:
    router.get("/reservations", list_reservations)
    router.post("/reservations", create_reservation)
    router.post("/reservations/expire", expire_reservations)
    router.get("/reservations/{id:int}", get_reservation)
    router.post("/reservations/{id:int}/fulfill", fulfill_reservation)
    router.post("/reservations/{id:int}/cancel", cancel_reservation)
