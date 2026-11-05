"""Helpers shared by handlers: optional JSON bodies, ETags and idempotent POST."""

from __future__ import annotations

import hashlib
import re
from functools import wraps
from typing import Any, Callable

from ..db import transaction
from ..errors import BadRequest, Conflict
from ..http.request import Request
from ..http.response import Response, json_response
from ..models import Product

_KEY = re.compile(r"[\x21-\x7e]{1,128}")
_ETAG = re.compile(r'(?:W/)?"?(\d+)"?')


def optional_json(request: Request) -> Any:
    """A body for actions where sending none is meaningful."""
    return request.json() if request.body else None


def etag_of(product: Product) -> str:
    return f'"{product.version}"'


def if_match_version(request: Request) -> int | None:
    """The version named by If-Match. ``*`` or no header gives ``None``."""
    header = request.header("If-Match")
    if header is None or header.strip() == "*":
        return None
    match = _ETAG.fullmatch(header.strip())
    if match is None:
        raise BadRequest("If-Match must hold a single version, such as \"3\"")
    return int(match[1])


def idempotent(handler: Callable[[Request], Response]) -> Callable[[Request], Response]:
    """Let clients retry a POST safely by sending an Idempotency-Key header.

    The first success is stored together with the effect it had, in one
    transaction, so a crash cannot leave one without the other. Replays get the
    stored response. A key reused for a different request is an error: replaying
    would answer a question the client did not ask. Failures are not stored, since
    a retry may legitimately succeed.
    """

    @wraps(handler)
    def wrapper(request: Request) -> Response:
        key = request.header("Idempotency-Key")
        if key is None:
            return handler(request)
        if not _KEY.fullmatch(key):
            raise BadRequest("Idempotency-Key must be 1 to 128 printable characters")
        digest = hashlib.sha256()
        digest.update(f"{request.method} {request.path}\n".encode("utf-8"))
        digest.update(request.body)
        fingerprint = digest.hexdigest()
        context = request.services.context
        with transaction(context.conn):
            stored = context.idempotency.find(key)
            if stored is not None:
                if stored["fingerprint"] != fingerprint:
                    raise Conflict(
                        "this Idempotency-Key was used for a different request",
                        code="idempotency_key_reused",
                    )
                return Response(
                    stored["status"],
                    bytes(stored["body"]),
                    {
                        "Content-Type": "application/json; charset=utf-8",
                        "Idempotency-Replayed": "true",
                    },
                )
            response = handler(request)
            if response.status < 400:
                context.idempotency.store(key, fingerprint, response.status, response.body, context.now())
            return response

    return wrapper


def created(data: Any, location: str) -> Response:
    return json_response(data, 201, {"Location": location})
