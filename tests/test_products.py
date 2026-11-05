import unittest

from support import ApiTestCase


class Creating(ApiTestCase):
    def test_create_and_fetch(self):
        reply = self.client.post(
            "/products",
            {"sku": "ABC-1", "name": "Widget", "unit_price_cents": 250, "reorder_point": 4},
        )
        product = self.ok(reply, 201)
        self.assertEqual(reply.headers["location"], f"/products/{product['id']}")
        self.assertEqual(reply.headers["etag"], '"1"')
        self.assertEqual(product["version"], 1)
        self.assertEqual(product["created_at"], "2024-03-01T12:00:00Z")
        self.assertEqual(self.ok(self.client.get(f"/products/{product['id']}")), product)

    def test_validation_errors_are_all_listed(self):
        reply = self.client.post("/products", {"sku": "bad sku!", "unit_price_cents": -1, "color": "red"})
        self.assertEqual(reply.status, 422)
        self.assertEqual(reply.error_code, "validation_failed")
        self.assertEqual(
            set(reply.json["error"]["details"]), {"sku", "name", "unit_price_cents", "color"}
        )

    def test_duplicate_sku_ignores_case(self):
        self.make_product("ABC")
        reply = self.client.post("/products", {"sku": "abc", "name": "x", "unit_price_cents": 1})
        self.assertEqual(reply.status, 409)
        self.assertEqual(reply.error_code, "duplicate_sku")

    def test_sku_of_a_deleted_product_can_be_reused(self):
        first = self.make_product("ABC")
        self.ok(self.client.delete(f"/products/{first['id']}"), 204)
        second = self.make_product("ABC")
        self.assertNotEqual(first["id"], second["id"])

    def test_body_format_errors(self):
        self.assertEqual(self.client.post("/products", raw=b"{oops").status, 400)
        self.assertEqual(self.client.post("/products", [1]).status, 422)
        reply = self.client.post("/products", {"sku": "A"}, headers={"Content-Type": "text/plain"})
        self.assertEqual(reply.status, 415)


class Reading(ApiTestCase):
    def test_missing_product(self):
        reply = self.client.get("/products/999")
        self.assertEqual(reply.status, 404)
        self.assertEqual(reply.error_code, "not_found")
        self.assertIn("request_id", reply.json["error"])

    def test_pagination_walks_everything_once(self):
        ids = [self.make_product(f"P{i}")["id"] for i in range(5)]
        seen, cursor = [], None
        for _ in range(5):
            path = "/products?limit=2" + (f"&cursor={cursor}" if cursor else "")
            page = self.ok(self.client.get(path))
            seen += [item["id"] for item in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, ids)

    def test_limit_bounds(self):
        self.assertEqual(self.client.get("/products?limit=0").status, 400)
        self.assertEqual(self.client.get("/products?limit=201").status, 400)
        self.assertEqual(self.client.get("/products?limit=abc").status, 400)

    def test_search_matches_sku_and_name(self):
        self.make_product("ALPHA", name="First thing")
        self.make_product("BETA", name="Second alpha thing")
        self.make_product("GAMMA", name="Third")
        items = self.ok(self.client.get("/products?search=alpha"))["items"]
        self.assertEqual([item["sku"] for item in items], ["ALPHA", "BETA"])

    def test_search_treats_wildcards_literally(self):
        self.make_product("A1", name="100% wool")
        self.make_product("A2", name="cotton")
        self.make_product("A_3", name="linen")
        self.assertEqual(len(self.ok(self.client.get("/products?search=%25"))["items"]), 1)
        self.assertEqual(len(self.ok(self.client.get("/products?search=_"))["items"]), 1)

    def test_deleted_products_are_hidden_unless_asked(self):
        product = self.make_product()
        self.ok(self.client.delete(f"/products/{product['id']}"), 204)
        self.assertEqual(self.ok(self.client.get("/products"))["items"], [])
        items = self.ok(self.client.get("/products?include_deleted=true"))["items"]
        self.assertEqual(items[0]["deleted_at"], "2024-03-01T12:00:00Z")
        self.assertEqual(self.client.get(f"/products/{product['id']}").status, 404)


class Updating(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.path = f"/products/{self.product['id']}"

    def patch(self, body, version='"1"'):
        return self.client.patch(self.path, body, headers={"If-Match": version})

    def test_change_bumps_version_and_timestamp(self):
        self.clock.advance(hours=1)
        reply = self.patch({"name": "New name"})
        updated = self.ok(reply)
        self.assertEqual(updated["name"], "New name")
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["updated_at"], "2024-03-01T13:00:00Z")
        self.assertEqual(updated["created_at"], self.product["created_at"])
        self.assertEqual(reply.headers["etag"], '"2"')

    def test_missing_if_match_is_428(self):
        reply = self.client.patch(self.path, {"name": "x"})
        self.assertEqual(reply.status, 428)

    def test_stale_version_is_412(self):
        self.ok(self.patch({"name": "first"}))
        reply = self.patch({"name": "second"})
        self.assertEqual(reply.status, 412)
        self.assertEqual(reply.json["error"]["details"]["current_version"], 2)
        self.assertEqual(self.ok(self.client.get(self.path))["name"], "first")

    def test_star_skips_the_check(self):
        self.ok(self.patch({"name": "first"}))
        self.ok(self.patch({"name": "second"}, version="*"))

    def test_unquoted_and_weak_tags_work(self):
        self.ok(self.patch({"name": "a"}, version="1"))
        self.ok(self.patch({"name": "b"}, version='W/"2"'))

    def test_malformed_if_match(self):
        self.assertEqual(self.patch({"name": "a"}, version="abc").status, 400)

    def test_no_real_change_keeps_the_version(self):
        updated = self.ok(self.patch({"name": self.product["name"]}))
        self.assertEqual(updated["version"], 1)

    def test_sku_collision(self):
        self.make_product("OTHER")
        reply = self.patch({"sku": "other"})
        self.assertEqual(reply.status, 409)

    def test_changing_only_the_case_of_own_sku(self):
        updated = self.ok(self.patch({"sku": "sku-1"}))
        self.assertEqual(updated["sku"], "sku-1")

    def test_price_is_validated_on_patch(self):
        self.assertEqual(self.patch({"unit_price_cents": -5}).status, 422)

    def test_cost_cannot_be_set_directly(self):
        self.assertEqual(self.patch({"avg_cost_cents": 5}).status, 422)

    def test_stock_arrival_does_not_invalidate_a_held_version(self):
        self.stock_in(self.product["id"], 5, unit_cost_cents=100)
        self.ok(self.patch({"name": "still fine"}))


class Deleting(ApiTestCase):
    def test_delete_is_soft_and_final(self):
        product = self.make_product()
        path = f"/products/{product['id']}"
        self.ok(self.client.delete(path), 204)
        self.assertEqual(self.client.delete(path).status, 404)

    def test_if_match_is_optional_but_honoured(self):
        product = self.make_product()
        path = f"/products/{product['id']}"
        self.assertEqual(self.client.delete(path, headers={"If-Match": '"7"'}).status, 412)
        self.ok(self.client.delete(path, headers={"If-Match": '"1"'}), 204)

    def test_active_reservation_blocks(self):
        product = self.make_product()
        self.stock_in(product["id"], 5)
        self.ok(self.reserve(product["id"], 1), 201)
        reply = self.client.delete(f"/products/{product['id']}")
        self.assertEqual(reply.status, 409)
        self.assertEqual(reply.error_code, "active_reservations")

    def test_expired_reservation_does_not_block(self):
        product = self.make_product()
        self.stock_in(product["id"], 5)
        self.ok(self.reserve(product["id"], 1, ttl_seconds=60), 201)
        self.clock.advance(seconds=61)
        self.ok(self.client.delete(f"/products/{product['id']}"), 204)

    def test_open_purchase_order_blocks(self):
        product = self.make_product()
        body = {"supplier": "ACME", "lines": [{"product_id": product["id"], "quantity": 1, "unit_cost_cents": 1}]}
        order = self.ok(self.client.post("/purchase-orders", body), 201)
        reply = self.client.delete(f"/products/{product['id']}")
        self.assertEqual(reply.error_code, "open_purchase_orders")
        self.ok(self.client.post(f"/purchase-orders/{order['id']}/cancel"))
        self.ok(self.client.delete(f"/products/{product['id']}"), 204)

    def test_stock_on_hand_does_not_block(self):
        product = self.make_product()
        self.stock_in(product["id"], 5)
        self.ok(self.client.delete(f"/products/{product['id']}"), 204)

    def test_history_of_a_deleted_product_stays_readable(self):
        product = self.make_product()
        self.stock_in(product["id"], 5)
        self.ok(self.client.delete(f"/products/{product['id']}"), 204)
        items = self.ok(self.client.get(f"/products/{product['id']}/movements"))["items"]
        self.assertEqual(len(items), 1)


class AuditTrail(ApiTestCase):
    def test_changes_are_recorded_with_the_request_id(self):
        reply = self.client.post(
            "/products",
            {"sku": "A", "name": "a", "unit_price_cents": 1},
            headers={"X-Request-Id": "trace-1"},
        )
        product = self.ok(reply, 201)
        self.ok(self.client.patch(f"/products/{product['id']}", {"name": "b"}, headers={"If-Match": "*"}))
        entries = self.ok(self.client.get(f"/audit?entity=product&entity_id={product['id']}"))["items"]
        self.assertEqual([e["action"] for e in entries], ["updated", "created"])
        self.assertEqual(entries[0]["detail"], {"changed": ["name"]})
        self.assertEqual(entries[1]["request_id"], "trace-1")

    def test_failed_change_leaves_no_entry(self):
        self.make_product("A")
        self.client.post("/products", {"sku": "A", "name": "a", "unit_price_cents": 1})
        self.assertEqual(len(self.ok(self.client.get("/audit"))["items"]), 1)

    def test_paging(self):
        for index in range(3):
            self.make_product(f"P{index}")
        first = self.ok(self.client.get("/audit?limit=2"))
        self.assertEqual(len(first["items"]), 2)
        second = self.ok(self.client.get(f"/audit?limit=2&cursor={first['next_cursor']}"))
        self.assertEqual(len(second["items"]), 1)
        self.assertIsNone(second["next_cursor"])


if __name__ == "__main__":
    unittest.main()
