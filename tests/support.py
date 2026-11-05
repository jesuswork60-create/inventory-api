"""A WSGI test client and a clock the tests can move by hand."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from inventory_api.app import Application
from inventory_api.db import Database
from inventory_api.migrations import migrate


class ManualClock:
    def __init__(self) -> None:
        self.moment = datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


class Reply:
    def __init__(self, status: str, headers: list[tuple[str, str]], body: bytes) -> None:
        self.status = int(status.split()[0])
        self.status_line = status
        self.headers = {name.lower(): value for name, value in headers}
        self.body = body

    @property
    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def error_code(self) -> str:
        return self.json["error"]["code"]


class Client:
    def __init__(self, app: Application) -> None:
        self.app = app

    def request(
        self, method: str, path: str, body: Any = None, *, headers: dict[str, str] | None = None, raw: bytes | None = None
    ) -> Reply:
        payload = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        route, _, query = path.partition("?")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": route,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(payload)),
            "wsgi.input": io.BytesIO(payload),
        }
        if payload:
            environ["CONTENT_TYPE"] = "application/json"
        for name, value in (headers or {}).items():
            key = name.upper().replace("-", "_")
            environ[key if key in ("CONTENT_TYPE", "CONTENT_LENGTH") else f"HTTP_{key}"] = value
        captured: dict[str, Any] = {}

        def start_response(status: str, response_headers: list[tuple[str, str]]) -> None:
            captured["status"] = status
            captured["headers"] = response_headers

        chunks = self.app(environ, start_response)
        return Reply(captured["status"], captured["headers"], b"".join(chunks))

    def get(self, path: str, **options: Any) -> Reply:
        return self.request("GET", path, **options)

    def post(self, path: str, body: Any = None, **options: Any) -> Reply:
        return self.request("POST", path, body, **options)

    def patch(self, path: str, body: Any = None, **options: Any) -> Reply:
        return self.request("PATCH", path, body, **options)

    def delete(self, path: str, **options: Any) -> Reply:
        return self.request("DELETE", path, **options)


class ApiTestCase(unittest.TestCase):
    """A migrated database in a temp folder, a client, and some shorthand."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = Database(os.path.join(folder.name, "test.db"))
        conn = self.database.connect()
        migrate(conn)
        conn.close()
        self.clock = ManualClock()
        self.client = Client(Application(self.database, clock=self.clock))

    def ok(self, reply: Reply, status: int = 200) -> Any:
        self.assertEqual(reply.status, status, reply.body[:500])
        return reply.json if reply.body else None

    def make_product(self, sku: str = "SKU-1", **values: Any) -> dict[str, Any]:
        body = {"sku": sku, "name": f"Product {sku}", "unit_price_cents": 1000, **values}
        return self.ok(self.client.post("/products", body), 201)

    def stock_in(self, product_id: int, quantity: int, **values: Any) -> dict[str, Any]:
        body = {"kind": "receipt", "quantity": quantity, **values}
        return self.ok(self.client.post(f"/products/{product_id}/movements", body), 201)

    def make_location(self, code: str = "B1", warehouse_id: int = 1) -> dict[str, Any]:
        return self.ok(self.client.post(f"/warehouses/{warehouse_id}/locations", {"code": code}), 201)

    def reserve(self, product_id: int, quantity: int, **values: Any) -> Reply:
        return self.client.post("/reservations", {"product_id": product_id, "quantity": quantity, **values})

    def available(self, product_id: int) -> int:
        return self.ok(self.client.get(f"/products/{product_id}/stock"))["available"]
