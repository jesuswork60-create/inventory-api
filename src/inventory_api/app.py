"""The WSGI application: layers wrapped around the router."""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from .clock import Clock, system_clock
from .db import Database
from .http.middleware import Next, access_log, compose, error_mapper, request_ids
from .http.request import Request
from .http.response import Response
from .http.router import Router
from .routes import register_all
from .services import Services

logger = logging.getLogger("inventory_api")


class Application:
    def __init__(self, database: Database, *, clock: Clock = system_clock) -> None:
        self.database = database
        self.clock = clock
        self.router = Router()
        register_all(self.router)
        self._handle = compose(
            self._dispatch,
            [request_ids, access_log(logger), error_mapper(logger), self._session],
        )

    def _session(self, next_handler: Next) -> Next:
        """Give the request its own connection, and close it however things end."""

        def handle(request: Request) -> Response:
            conn = self.database.connect()
            try:
                request.services = Services(conn, self.clock, request.request_id)
                return next_handler(request)
            finally:
                conn.close()

        return handle

    def _dispatch(self, request: Request) -> Response:
        handler, request.params = self.router.resolve(request.method, request.path)
        return handler(request)

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        request = Request(environ)
        response = self._handle(request)
        start_response(response.status_line, response.header_list())
        if request.method == "HEAD" or response.status == 204:
            return [b""]
        return [response.body]
