from __future__ import annotations

from ..http.request import Request
from ..http.response import Response, json_response
from ..http.router import Router
from .common import created


def list_warehouses(request: Request) -> Response:
    rows = request.services.locations.list_warehouses()
    return json_response({"items": [row.to_json() for row in rows]})


def create_warehouse(request: Request) -> Response:
    warehouse = request.services.locations.create_warehouse(request.json())
    return created(warehouse.to_json(), f"/warehouses/{warehouse.id}")


def get_warehouse(request: Request) -> Response:
    return json_response(request.services.locations.require_warehouse(request.params["id"]).to_json())


def list_locations(request: Request) -> Response:
    rows = request.services.locations.list_locations(request.params["id"])
    return json_response({"items": [row.to_json() for row in rows]})


def create_location(request: Request) -> Response:
    location = request.services.locations.create_location(request.params["id"], request.json())
    return created(location.to_json(), f"/locations/{location.id}")


def get_location(request: Request) -> Response:
    return json_response(request.services.locations.require_location(request.params["id"]).to_json())


def deactivate_location(request: Request) -> Response:
    return json_response(request.services.locations.deactivate_location(request.params["id"]).to_json())


def activate_location(request: Request) -> Response:
    return json_response(request.services.locations.activate_location(request.params["id"]).to_json())


def register(router: Router) -> None:
    router.get("/warehouses", list_warehouses)
    router.post("/warehouses", create_warehouse)
    router.get("/warehouses/{id:int}", get_warehouse)
    router.get("/warehouses/{id:int}/locations", list_locations)
    router.post("/warehouses/{id:int}/locations", create_location)
    router.get("/locations/{id:int}", get_location)
    router.post("/locations/{id:int}/deactivate", deactivate_location)
    router.post("/locations/{id:int}/activate", activate_location)
