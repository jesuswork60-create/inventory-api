"""The WSGI surface, error handling, the command line and one real socket test."""

import contextlib
import http.client
import io
import json
import logging
import os
import tempfile
import threading
import unittest

from support import ApiTestCase, Client

from inventory_api.app import Application
from inventory_api.cli import build_parser, create_server, main
from inventory_api.db import Database
from inventory_api.errors import BadRequest
from inventory_api.http.middleware import access_log, error_mapper
from inventory_api.http.request import Request
from inventory_api.http.response import Response
from inventory_api.migrations import migrate
from inventory_api.seed import seed


class Surface(ApiTestCase):
    def test_health(self):
        self.assertEqual(self.ok(self.client.get("/health"))["status"], "ok")

    def test_health_notices_an_unmigrated_database(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client(Application(Database(os.path.join(folder, "new.db"))))
            reply = client.get("/health")
            self.assertEqual(reply.status, 503)
            self.assertEqual(reply.json["status"], "needs_migration")

    def test_unknown_path(self):
        self.assertEqual(self.client.get("/nowhere").error_code, "not_found")

    def test_wrong_method_sets_allow(self):
        reply = self.client.delete("/products")
        self.assertEqual(reply.status, 405)
        self.assertEqual(reply.headers["allow"], "GET, POST")

    def test_head_has_headers_but_no_body(self):
        product = self.make_product()
        reply = self.client.request("HEAD", f"/products/{product['id']}")
        self.assertEqual(reply.status, 200)
        self.assertEqual(reply.body, b"")
        self.assertGreater(int(reply.headers["content-length"]), 0)

    def test_request_id_is_generated_when_absent(self):
        self.assertEqual(len(self.client.get("/health").headers["x-request-id"]), 32)

    def test_good_request_id_is_echoed(self):
        reply = self.client.get("/health", headers={"X-Request-Id": "abc-123"})
        self.assertEqual(reply.headers["x-request-id"], "abc-123")

    def test_unsafe_request_id_is_replaced(self):
        reply = self.client.get("/health", headers={"X-Request-Id": "a b\nc"})
        self.assertNotIn(" ", reply.headers["x-request-id"])
        self.assertEqual(len(reply.headers["x-request-id"]), 32)

    def test_errors_carry_the_request_id_too(self):
        reply = self.client.get("/nowhere", headers={"X-Request-Id": "trace"})
        self.assertEqual(reply.json["error"]["request_id"], "trace")
        self.assertEqual(reply.headers["x-request-id"], "trace")

    def test_no_content_has_an_empty_body(self):
        product = self.make_product()
        self.assertEqual(self.client.delete(f"/products/{product['id']}").body, b"")

    def test_non_ascii_text_round_trips(self):
        product = self.make_product(name="Café crème")
        self.assertEqual(self.ok(self.client.get(f"/products/{product['id']}"))["name"], "Café crème")


def bare_request():
    return Request({"REQUEST_METHOD": "GET", "PATH_INFO": "/x"})


class ErrorMapping(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("inventory_api.test")
        self.logger.propagate = False
        self.logger.addHandler(logging.NullHandler())

    def run_with(self, exception):
        def handler(request):
            raise exception

        return error_mapper(self.logger)(handler)(bare_request())

    def test_api_error(self):
        reply = self.run_with(BadRequest("nope"))
        self.assertEqual(reply.status, 400)
        self.assertEqual(json.loads(reply.body)["error"]["code"], "bad_request")

    def test_unexpected_error_hides_details(self):
        reply = self.run_with(RuntimeError("secret path"))
        self.assertEqual(reply.status, 500)
        self.assertNotIn(b"secret", reply.body)

    def test_integrity_error_is_a_conflict(self):
        import sqlite3

        self.assertEqual(self.run_with(sqlite3.IntegrityError("x")).status, 409)

    def test_locked_database_is_retryable(self):
        import sqlite3

        self.assertEqual(self.run_with(sqlite3.OperationalError("database is locked")).status, 503)
        self.assertEqual(self.run_with(sqlite3.OperationalError("no such table")).status, 500)

    def test_access_log_records_status_and_time(self):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("inventory_api.test.access")
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.addHandler(Capture())
        ticks = iter([1.0, 1.25])
        layer = access_log(logger, timer=lambda: next(ticks))
        layer(lambda request: Response(204))(bare_request())
        self.assertEqual(records[0].split()[:4], ["GET", "/x", "204", "250.0ms"])


class Failures(ApiTestCase):
    def test_a_bug_in_a_handler_rolls_back_and_reports_500(self):
        product = self.make_product()["id"]
        logger = logging.getLogger("inventory_api")
        previous = logger.disabled
        logger.disabled = True
        self.addCleanup(setattr, logger, "disabled", previous)

        def broken(request):
            services = request.services
            from inventory_api.db import transaction

            with transaction(services.context.conn):
                services.stock.apply(product, 1, "receipt", 5)
                raise RuntimeError("boom")

        self.client.app.router.post("/broken", broken)
        self.assertEqual(self.client.post("/broken").status, 500)
        self.assertEqual(self.available(product), 0)


class CommandLine(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "cli.db")

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--db", self.path, *argv])
        return code, out.getvalue()

    def test_migrate_status_and_target(self):
        code, out = self.run_cli("migrate", "--status")
        self.assertEqual(code, 0)
        self.assertIn("pending 1 products", out)
        code, out = self.run_cli("migrate", "--target", "1")
        self.assertIn("applied 1 products", out)
        self.assertNotIn("applied 2", out)
        code, out = self.run_cli("migrate")
        self.assertIn("applied 2", out)
        code, out = self.run_cli("migrate")
        self.assertIn("nothing to do", out)
        code, out = self.run_cli("migrate", "--status")
        self.assertIn("up to date", out)

    def test_seed_runs_once(self):
        code, out = self.run_cli("seed")
        self.assertEqual(code, 0)
        self.assertIn("added 4", out)
        code, out = self.run_cli("seed")
        self.assertIn("nothing added", out)

    def test_seeded_data_is_usable(self):
        self.run_cli("seed")
        client = Client(Application(Database(self.path)))
        report = client.get("/reports/low-stock").json["items"]
        self.assertEqual({row["sku"] for row in report}, {"WID-BLU", "GIZ-100"})
        self.assertGreater(client.get("/reports/valuation").json["total_value_cents"], 0)

    def test_parser_requires_a_command(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            build_parser().parse_args([])

    def test_seed_function_refuses_to_double_up(self):
        database = Database(self.path)
        conn = database.connect()
        self.addCleanup(conn.close)
        migrate(conn)
        self.assertEqual(seed(conn), 4)
        self.assertEqual(seed(conn), 0)


class RealSocket(unittest.TestCase):
    """The only test using the network stack; the OS picks a free port."""

    def test_round_trip_over_http(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Database(os.path.join(folder, "net.db"))
            conn = database.connect()
            migrate(conn)
            conn.close()
            with create_server(database, "127.0.0.1", 0) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                    body = json.dumps({"sku": "NET", "name": "Over the wire", "unit_price_cents": 5})
                    client.request("POST", "/products", body, {"Content-Type": "application/json"})
                    reply = client.getresponse()
                    self.assertEqual(reply.status, 201)
                    created = json.loads(reply.read())
                    client.request("GET", f"/products/{created['id']}")
                    reply = client.getresponse()
                    self.assertEqual(json.loads(reply.read())["sku"], "NET")
                    client.close()
                finally:
                    server.shutdown()
                    thread.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
