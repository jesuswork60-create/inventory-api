"""Layers around the router. A middleware takes the next handler and returns one."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import uuid
from typing import Callable, Iterable

from ..errors import ApiError, Conflict, MethodNotAllowed, ServiceUnavailable
from .request import Request
from .response import Response, json_response

Next = Callable[[Request], Response]
Middleware = Callable[[Next], Next]

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def compose(handler: Next, layers: Iterable[Middleware]) -> Next:
    """Wrap ``handler`` so the first layer listed is the outermost."""
    for layer in reversed(list(layers)):
        handler = layer(handler)
    return handler


def request_ids(next_handler: Next) -> Next:
    """Give each request an id, keeping a well-formed one sent by the client."""

    def handle(request: Request) -> Response:
        offered = request.header("X-Request-Id")
        if offered and _SAFE_REQUEST_ID.fullmatch(offered):
            request.request_id = offered
        else:
            request.request_id = uuid.uuid4().hex
        response = next_handler(request)
        response.headers["X-Request-Id"] = request.request_id
        return response

    return handle


def access_log(logger: logging.Logger, timer: Callable[[], float] = time.monotonic) -> Middleware:
    def layer(next_handler: Next) -> Next:
        def handle(request: Request) -> Response:
            started = timer()
            response = next_handler(request)
            logger.info(
                "%s %s %d %.1fms id=%s",
                request.method,
                request.path,
                response.status,
                (timer() - started) * 1000,
                request.request_id,
            )
            return response

        return handle

    return layer


def _error_response(error: ApiError, request: Request) -> Response:
    payload = {"code": error.code, "message": error.message, "request_id": request.request_id}
    if error.details is not None:
        payload["details"] = error.details
    headers = {}
    if isinstance(error, MethodNotAllowed):
        headers["Allow"] = ", ".join(error.allowed)
    return json_response({"error": payload}, error.status, headers)


def error_mapper(logger: logging.Logger) -> Middleware:
    """Turn exceptions into JSON. Unexpected ones are logged and kept vague."""

    def layer(next_handler: Next) -> Next:
        def handle(request: Request) -> Response:
            try:
                return next_handler(request)
            except ApiError as error:
                return _error_response(error, request)
            except sqlite3.IntegrityError as exc:
                # Services check first; this catches a constraint they could not foresee.
                logger.warning("integrity error on %s %s: %s", request.method, request.path, exc)
                return _error_response(Conflict("the change conflicts with existing data"), request)
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc) or "busy" in str(exc):
                    return _error_response(ServiceUnavailable("the database is busy, try again"), request)
                logger.exception("database failure on %s %s", request.method, request.path)
                return _error_response(ApiError("something went wrong"), request)
            except Exception:
                logger.exception("unhandled error on %s %s", request.method, request.path)
                return _error_response(ApiError("something went wrong"), request)

        return handle

    return layer
