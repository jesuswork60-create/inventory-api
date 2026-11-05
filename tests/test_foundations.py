"""Router, request parsing, validation, migrations and pagination on their own."""

import io
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from inventory_api.clock import add_seconds, parse_stamp, to_stamp
from inventory_api.db import Database, transaction
from inventory_api.errors import (
    ApiError,
    BadRequest,
    MethodNotAllowed,
    NotFound,
    PayloadTooLarge,
    UnsupportedMediaType,
    ValidationFailed,
)
from inventory_api.http.middleware import compose
from inventory_api.http.request import MAX_BODY_BYTES, Request
from inventory_api.http.response import Response, json_response, no_content
from inventory_api.http.router import Router
from inventory_api.migrations import MIGRATIONS, Migration, MigrationError, applied_versions, migrate, pending
from inventory_api.pagination import Page, paginate
from inventory_api.services.stock import weighted_average
from inventory_api.validation import Integer, ListOf, Schema, Text, Timestamp


class Routing(unittest.TestCase):
    def setUp(self):
        self.router = Router()
        self.router.get("/items", "list")
        self.router.post("/items", "create")
        self.router.get("/items/special", "special")
        self.router.get("/items/{id:int}", "one")
        self.router.get("/items/{id:int}/parts/{name}", "part")

    def test_literal_path(self):
        self.assertEqual(self.router.resolve("GET", "/items"), ("list", {}))

    def test_methods_share_a_path(self):
        self.assertEqual(self.router.resolve("POST", "/items")[0], "create")

    def test_int_conversion(self):
        self.assertEqual(self.router.resolve("GET", "/items/42"), ("one", {"id": 42}))

    def test_several_parameters(self):
        self.assertEqual(
            self.router.resolve("GET", "/items/1/parts/wheel"), ("part", {"id": 1, "name": "wheel"})
        )

    def test_literal_registered_first_wins(self):
        self.assertEqual(self.router.resolve("GET", "/items/special")[0], "special")

    def test_non_numeric_id_is_not_found(self):
        with self.assertRaises(NotFound):
            self.router.resolve("GET", "/items/abc")

    def test_negative_id_is_not_found(self):
        with self.assertRaises(NotFound):
            self.router.resolve("GET", "/items/-1")

    def test_id_beyond_sqlite_range_is_not_found(self):
        with self.assertRaises(NotFound):
            self.router.resolve("GET", f"/items/{2**63}")

    def test_placeholder_does_not_cross_slashes(self):
        with self.assertRaises(NotFound):
            self.router.resolve("GET", "/items/1/parts/a/b")

    def test_trailing_slash_is_a_different_path(self):
        with self.assertRaises(NotFound):
            self.router.resolve("GET", "/items/")

    def test_wrong_method_lists_what_is_allowed(self):
        with self.assertRaises(MethodNotAllowed) as caught:
            self.router.resolve("DELETE", "/items")
        self.assertEqual(caught.exception.allowed, ["GET", "POST"])

    def test_head_uses_get(self):
        self.assertEqual(self.router.resolve("HEAD", "/items")[0], "list")

    def test_duplicate_registration_is_a_mistake(self):
        with self.assertRaises(ValueError):
            self.router.get("/items", "again")

    def test_unknown_converter_is_a_mistake(self):
        with self.assertRaises(ValueError):
            self.router.get("/x/{id:uuid}", "x")

    def test_template_literals_are_escaped(self):
        self.router.get("/a.b", "dotted")
        with self.assertRaises(NotFound):
            self.router.resolve("GET", "/aXb")


def make_request(body=b"", content_type="application/json", query="", **extra):
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/x",
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": content_type,
        "wsgi.input": io.BytesIO(body),
    }
    environ.update(extra)
    return Request(environ)


class Requests(unittest.TestCase):
    def test_json_body(self):
        self.assertEqual(make_request(b'{"a": 1}').json(), {"a": 1})

    def test_charset_in_content_type(self):
        self.assertEqual(make_request(b"[]", "application/json; charset=utf-8").json(), [])

    def test_wrong_content_type(self):
        with self.assertRaises(UnsupportedMediaType):
            make_request(b"{}", "text/plain").json()

    def test_bad_json(self):
        with self.assertRaises(BadRequest):
            make_request(b"{nope").json()

    def test_bad_encoding(self):
        with self.assertRaises(BadRequest):
            make_request(b'"\xff"').json()

    def test_empty_body(self):
        with self.assertRaises(BadRequest):
            make_request(b"").json()

    def test_oversized_body_is_refused_without_reading(self):
        request = make_request(b"", CONTENT_LENGTH=str(MAX_BODY_BYTES + 1))
        with self.assertRaises(PayloadTooLarge):
            request.body

    def test_bad_content_length(self):
        with self.assertRaises(BadRequest):
            make_request(b"", CONTENT_LENGTH="many").body
        with self.assertRaises(BadRequest):
            make_request(b"", CONTENT_LENGTH="-1").body

    def test_headers(self):
        request = make_request(b"", HTTP_IF_MATCH='"2"')
        self.assertEqual(request.header("If-Match"), '"2"')
        self.assertEqual(request.header("content-type"), "application/json")
        self.assertIsNone(request.header("X-Missing"))

    def test_query_int(self):
        request = make_request(query="a=5&b=x&c=")
        self.assertEqual(request.query_int("a"), 5)
        self.assertEqual(request.query_int("c", 9), 9)
        self.assertEqual(request.query_int("absent", 7), 7)
        with self.assertRaises(BadRequest):
            request.query_int("b")
        with self.assertRaises(BadRequest):
            request.query_int("a", maximum=4)
        with self.assertRaises(BadRequest):
            request.query_int("a", minimum=6)

    def test_query_bool(self):
        request = make_request(query="a=true&b=0&c=maybe")
        self.assertTrue(request.query_bool("a"))
        self.assertFalse(request.query_bool("b"))
        self.assertTrue(request.query_bool("absent", True))
        with self.assertRaises(BadRequest):
            request.query_bool("c")

    def test_query_time_accepts_an_unescaped_plus(self):
        request = make_request(query="t=2024-03-01T12:00:00+02:00")
        self.assertEqual(request.query_time("t"), "2024-03-01T10:00:00Z")
        with self.assertRaises(BadRequest):
            make_request(query="t=later").query_time("t")


class Responses(unittest.TestCase):
    def test_status_line_and_length(self):
        response = json_response({"a": 1}, 201)
        self.assertEqual(response.status_line, "201 Created")
        self.assertIn(("Content-Length", str(len(response.body))), response.header_list())

    def test_no_content_has_no_length(self):
        self.assertNotIn("Content-Length", dict(no_content().header_list()))

    def test_unknown_status_code(self):
        self.assertEqual(Response(599).status_line, "599 Unknown")

    def test_compose_order(self):
        order = []

        def layer(name):
            def wrap(next_handler):
                def handle(request):
                    order.append(name)
                    return next_handler(request)

                return handle

            return wrap

        compose(lambda request: Response(), [layer("outer"), layer("inner")])(None)
        self.assertEqual(order, ["outer", "inner"])


class Times(unittest.TestCase):
    def test_to_stamp_converts_to_utc(self):
        moment = datetime(2024, 3, 1, 12, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(to_stamp(moment), "2024-03-01T10:00:00Z")

    def test_to_stamp_refuses_naive(self):
        with self.assertRaises(ValueError):
            to_stamp(datetime(2024, 3, 1))

    def test_parse_forms(self):
        self.assertEqual(parse_stamp("2024-03-01"), "2024-03-01T00:00:00Z")
        self.assertEqual(parse_stamp("2024-03-01T12:00:00Z"), "2024-03-01T12:00:00Z")
        self.assertEqual(parse_stamp("2024-03-01T12:00:00-01:00"), "2024-03-01T13:00:00Z")

    def test_time_without_offset_is_refused(self):
        with self.assertRaises(ValueError):
            parse_stamp("2024-03-01T12:00:00")

    def test_add_seconds(self):
        moment = datetime(2024, 3, 1, 23, 59, 30, tzinfo=timezone.utc)
        self.assertEqual(add_seconds(moment, 60), "2024-03-02T00:00:30Z")


class Validation(unittest.TestCase):
    SCHEMA = Schema(
        {
            "name": Text(required=True, min_length=1, max_length=5),
            "count": Integer(minimum=0, maximum=10, nonzero=True),
            "kind": Text(choices=("a", "b")),
            "code": Text(pattern=r"[A-Z]+"),
            "when": Timestamp(),
        }
    )

    def errors(self, data, **options):
        with self.assertRaises(ValidationFailed) as caught:
            self.SCHEMA.load(data, **options)
        return caught.exception.errors

    def test_valid_data_is_cleaned(self):
        out = self.SCHEMA.load({"name": "  ab ", "count": 3, "when": "2024-01-01"})
        self.assertEqual(out, {"name": "ab", "count": 3, "when": "2024-01-01T00:00:00Z"})

    def test_required(self):
        self.assertEqual(self.errors({})["name"], "is required")

    def test_partial_allows_missing_but_still_checks_given(self):
        self.assertEqual(self.SCHEMA.load({}, partial=True), {})
        self.assertIn("count", self.errors({"count": 99}, partial=True))

    def test_unknown_field(self):
        self.assertEqual(self.errors({"name": "a", "extra": 1})["extra"], "is not a known field")

    def test_null_is_refused(self):
        self.assertIn("null", self.errors({"name": None})["name"])

    def test_all_problems_reported_together(self):
        found = self.errors({"name": "too long a name", "count": "x", "kind": "c", "code": "abc", "when": "?"})
        self.assertEqual(set(found), {"name", "count", "kind", "code", "when"})

    def test_blank_after_strip_is_empty(self):
        self.assertIn("name", self.errors({"name": "   "}))

    def test_integer_rejects_bool_and_float(self):
        self.assertIn("count", self.errors({"name": "a", "count": True}))
        self.assertIn("count", self.errors({"name": "a", "count": 2.0}))

    def test_integer_bounds_and_zero(self):
        self.assertIn("at least", self.errors({"name": "a", "count": -1})["count"])
        self.assertIn("at most", self.errors({"name": "a", "count": 11})["count"])
        self.assertIn("zero", self.errors({"name": "a", "count": 0})["count"])

    def test_body_must_be_an_object(self):
        self.assertEqual(self.errors([1, 2]), {"body": "must be an object"})

    def test_nested_list_errors_carry_a_path(self):
        schema = Schema(
            {"lines": ListOf(Schema({"qty": Integer(required=True, minimum=1)}), required=True, min_items=1)}
        )
        self.assertEqual(schema.load({"lines": [{"qty": 1}]}), {"lines": [{"qty": 1}]})
        with self.assertRaises(ValidationFailed) as caught:
            schema.load({"lines": [{"qty": 1}, {"qty": 0}, {}, 5]})
        errors = caught.exception.errors
        self.assertIn("at least", errors["lines[1].qty"])
        self.assertEqual(errors["lines[2].qty"], "is required")
        self.assertEqual(errors["lines[3]"], "must be an object")
        with self.assertRaises(ValidationFailed):
            schema.load({"lines": []})
        with self.assertRaises(ValidationFailed):
            schema.load({"lines": "no"})


class Migrations(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.conn = Database(os.path.join(folder.name, "m.db")).connect()
        self.addCleanup(self.conn.close)

    def test_all_apply_once(self):
        done = migrate(self.conn)
        self.assertEqual(len(done), len(MIGRATIONS))
        self.assertEqual(migrate(self.conn), [])
        self.assertEqual(pending(self.conn), [])

    def test_target_stops_early_and_resumes(self):
        migrate(self.conn, target=2)
        self.assertEqual(applied_versions(self.conn), [1, 2])
        self.assertEqual(len(pending(self.conn)), len(MIGRATIONS) - 2)
        migrate(self.conn)
        self.assertEqual(pending(self.conn), [])

    def test_failure_rolls_back_only_that_migration(self):
        migrations = (
            Migration(1, "good", ("CREATE TABLE one (x)",)),
            Migration(2, "bad", ("CREATE TABLE two (x)", "THIS IS NOT SQL")),
        )
        with self.assertRaises(MigrationError) as caught:
            migrate(self.conn, migrations=migrations)
        self.assertIn("bad", str(caught.exception))
        self.assertEqual(applied_versions(self.conn), [1])
        names = {r["name"] for r in self.conn.execute("SELECT name FROM sqlite_master")}
        self.assertIn("one", names)
        self.assertNotIn("two", names)

    def test_newer_database_is_refused(self):
        migrate(self.conn)
        with self.assertRaises(MigrationError):
            migrate(self.conn, migrations=MIGRATIONS[:1])

    def test_gaps_in_the_sequence_are_a_mistake(self):
        with self.assertRaises(MigrationError):
            migrate(self.conn, migrations=(Migration(2, "skip", ()),))

    def test_default_location_exists(self):
        migrate(self.conn)
        row = self.conn.execute("SELECT code FROM locations WHERE id = 1").fetchone()
        self.assertEqual(row["code"], "DEFAULT")

    def test_foreign_keys_are_enforced(self):
        migrate(self.conn)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO stock_levels VALUES (999, 999, 1)")


class Transactions(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.conn = Database(os.path.join(folder.name, "t.db")).connect()
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE t (x)")

    def count(self):
        return self.conn.execute("SELECT COUNT(*) AS c FROM t").fetchone()["c"]

    def test_commit(self):
        with transaction(self.conn):
            self.conn.execute("INSERT INTO t VALUES (1)")
        self.assertEqual(self.count(), 1)

    def test_rollback_on_error(self):
        with self.assertRaises(RuntimeError):
            with transaction(self.conn):
                self.conn.execute("INSERT INTO t VALUES (1)")
                raise RuntimeError
        self.assertEqual(self.count(), 0)

    def test_inner_block_joins_outer(self):
        with self.assertRaises(RuntimeError):
            with transaction(self.conn):
                with transaction(self.conn):
                    self.conn.execute("INSERT INTO t VALUES (1)")
                raise RuntimeError
        self.assertEqual(self.count(), 0)


class Paging(unittest.TestCase):
    def render(self, rows, limit):
        return paginate(rows, Page(limit, None), cursor_of=lambda x: x, render=str)

    def test_more_rows_give_a_cursor(self):
        self.assertEqual(self.render([1, 2, 3], 2), {"items": ["1", "2"], "next_cursor": 2})

    def test_last_page_has_none(self):
        self.assertEqual(self.render([1, 2], 2)["next_cursor"], None)
        self.assertEqual(self.render([], 2), {"items": [], "next_cursor": None})


class AverageCost(unittest.TestCase):
    def test_first_arrival_sets_the_cost(self):
        self.assertEqual(weighted_average(0, 0, 10, 500), 500)

    def test_blends_by_quantity(self):
        self.assertEqual(weighted_average(10, 100, 30, 200), 175)

    def test_rounds_half_up(self):
        self.assertEqual(weighted_average(1, 100, 1, 101), 101)
        self.assertEqual(weighted_average(2, 100, 1, 101), 100)

    def test_zero_units_keeps_the_cost(self):
        self.assertEqual(weighted_average(0, 123, 0, 999), 123)


class ErrorTypes(unittest.TestCase):
    def test_codes_can_be_overridden(self):
        self.assertEqual(ApiError("x", code="other").code, "other")
        self.assertEqual(NotFound("x").code, "not_found")


if __name__ == "__main__":
    unittest.main()
