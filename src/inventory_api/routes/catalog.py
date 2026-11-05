from __future__ import annotations

from ..errors import PreconditionRequired
from ..http.request import Request
from ..http.response import Response, json_response, no_content
from ..http.router import Router
from ..pagination import page_from_request, paginate
from .common import etag_of, idempotent, if_match_version


@idempotent
def create_product(request: Request) -> Response:
    product = request.services.catalog.create(request.json())
    return json_response(
        product.to_json(),
        201,
        {"Location": f"/products/{product.id}", "ETag": etag_of(product)},
    )


def list_products(request: Request) -> Response:
    page = page_from_request(request)
    rows = request.services.catalog.list(
        limit=page.fetch_size,
        cursor=page.cursor,
        search=request.first("search"),
        include_deleted=request.query_bool("include_deleted"),
    )
    return json_response(paginate(rows, page, cursor_of=lambda p: p.id, render=lambda p: p.to_json()))


def get_product(request: Request) -> Response:
    product = request.services.catalog.require(request.params["id"])
    return json_response(product.to_json(), headers={"ETag": etag_of(product)})


def patch_product(request: Request) -> Response:
    if request.header("If-Match") is None:
        raise PreconditionRequired('send If-Match with the version you read, such as "3"')
    product = request.services.catalog.update(
        request.params["id"], request.json(), if_match_version(request)
    )
    return json_response(product.to_json(), headers={"ETag": etag_of(product)})


def delete_product(request: Request) -> Response:
    request.services.catalog.delete(request.params["id"], if_match_version(request))
    return no_content()


def register(router: Router) -> None:
    router.get("/products", list_products)
    router.post("/products", create_product)
    router.get("/products/{id:int}", get_product)
    router.patch("/products/{id:int}", patch_product)
    router.delete("/products/{id:int}", delete_product)


