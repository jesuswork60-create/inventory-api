import unittest

from support import ApiTestCase


class Movements(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()["id"]
        self.path = f"/products/{self.product}/movements"

    def post(self, **body):
        return self.client.post(self.path, body)

    def test_receipt_raises_levels(self):
        result = self.stock_in(self.product, 10)
        self.assertEqual((result["on_hand"], result["available"]), (10, 10))
        self.assertEqual(result["movement"]["quantity"], 10)

    def test_shipment_lowers_levels_and_records_negative(self):
        self.stock_in(self.product, 10)
        result = self.ok(self.post(kind="shipment", quantity=3), 201)
        self.assertEqual(result["on_hand"], 7)
        self.assertEqual(result["movement"]["quantity"], -3)

    def test_cannot_ship_more_than_available(self):
        self.stock_in(self.product, 2)
        reply = self.post(kind="shipment", quantity=3)
        self.assertEqual(reply.status, 409)
        self.assertEqual(reply.error_code, "insufficient_stock")
        self.assertEqual(reply.json["error"]["details"], {"available": 2, "requested": 3})
        self.assertEqual(self.available(self.product), 2)

    def test_reservations_reduce_what_can_ship(self):
        self.stock_in(self.product, 5)
        self.ok(self.reserve(self.product, 4), 201)
        self.assertEqual(self.post(kind="shipment", quantity=2).status, 409)
        self.ok(self.post(kind="shipment", quantity=1), 201)

    def test_scrap_and_return(self):
        self.stock_in(self.product, 5)
        self.assertEqual(self.ok(self.post(kind="scrap", quantity=2), 201)["on_hand"], 3)
        self.assertEqual(self.ok(self.post(kind="return", quantity=1), 201)["on_hand"], 4)

    def test_only_adjustments_may_be_negative(self):
        self.stock_in(self.product, 5)
        self.assertEqual(self.post(kind="receipt", quantity=-1).status, 400)
        self.assertEqual(self.post(kind="shipment", quantity=-1).status, 400)
        self.assertEqual(self.ok(self.post(kind="adjustment", quantity=-1), 201)["on_hand"], 4)

    def test_adjustment_may_cut_into_reservations_but_not_below_zero(self):
        self.stock_in(self.product, 5)
        self.ok(self.reserve(self.product, 4), 201)
        result = self.ok(self.post(kind="adjustment", quantity=-3), 201)
        self.assertEqual((result["on_hand"], result["available"]), (2, -2))
        reply = self.post(kind="adjustment", quantity=-3)
        self.assertEqual(reply.error_code, "insufficient_stock")

    def test_zero_and_unknown_kinds_are_invalid(self):
        self.assertEqual(self.post(kind="receipt", quantity=0).status, 422)
        self.assertEqual(self.post(kind="teleport", quantity=1).status, 422)
        self.assertEqual(self.post(kind="transfer_in", quantity=1).status, 422)

    def test_unit_cost_only_on_incoming(self):
        self.stock_in(self.product, 5)
        self.assertEqual(self.post(kind="shipment", quantity=1, unit_cost_cents=5).status, 400)
        self.assertEqual(self.post(kind="return", quantity=1, unit_cost_cents=5).status, 400)
        self.ok(self.post(kind="adjustment", quantity=1, unit_cost_cents=5), 201)

    def test_unknown_product_and_location(self):
        self.assertEqual(self.client.post("/products/999/movements", {"kind": "receipt", "quantity": 1}).status, 404)
        self.assertEqual(self.post(kind="receipt", quantity=1, location_id=999).status, 404)

    def test_deleted_product_refuses_movements(self):
        self.ok(self.client.delete(f"/products/{self.product}"), 204)
        self.assertEqual(self.post(kind="receipt", quantity=1).status, 404)

    def test_listing_is_newest_first_with_filters(self):
        self.stock_in(self.product, 5)
        self.clock.advance(days=1)
        self.ok(self.post(kind="shipment", quantity=1), 201)
        self.clock.advance(days=1)
        self.ok(self.post(kind="shipment", quantity=1), 201)
        rows = self.ok(self.client.get(self.path))["items"]
        self.assertEqual([r["kind"] for r in rows], ["shipment", "shipment", "receipt"])
        rows = self.ok(self.client.get(f"{self.path}?kind=receipt"))["items"]
        self.assertEqual(len(rows), 1)
        rows = self.ok(self.client.get(f"{self.path}?since=2024-03-02T00:00:00Z"))["items"]
        self.assertEqual(len(rows), 2)
        rows = self.ok(self.client.get(f"{self.path}?until=2024-03-02T12:00:00Z"))["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.client.get(f"{self.path}?kind=teleport").status, 400)
        self.assertEqual(self.client.get(f"{self.path}?since=soon").status, 400)

    def test_listing_pages_backwards_through_history(self):
        for _ in range(5):
            self.stock_in(self.product, 1)
        first = self.ok(self.client.get(f"{self.path}?limit=2"))
        second = self.ok(self.client.get(f"{self.path}?limit=2&cursor={first['next_cursor']}"))
        third = self.ok(self.client.get(f"{self.path}?limit=2&cursor={second['next_cursor']}"))
        ids = [m["id"] for page in (first, second, third) for m in page["items"]]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(len(set(ids)), 5)
        self.assertIsNone(third["next_cursor"])

    def test_ledger_always_adds_up_to_the_level(self):
        self.stock_in(self.product, 10)
        self.ok(self.post(kind="shipment", quantity=3), 201)
        self.ok(self.post(kind="adjustment", quantity=2), 201)
        self.ok(self.post(kind="scrap", quantity=1), 201)
        rows = self.ok(self.client.get(self.path))["items"]
        summary = self.ok(self.client.get(f"/products/{self.product}/stock"))
        self.assertEqual(sum(r["quantity"] for r in rows), summary["on_hand"])


class AverageCostTracking(ApiTestCase):
    def test_receipts_blend_cost(self):
        product = self.make_product()["id"]
        self.stock_in(product, 10, unit_cost_cents=100)
        self.stock_in(product, 30, unit_cost_cents=200)
        self.assertEqual(self.ok(self.client.get(f"/products/{product}"))["avg_cost_cents"], 175)

    def test_receipt_without_a_cost_leaves_the_average(self):
        product = self.make_product()["id"]
        self.stock_in(product, 10, unit_cost_cents=100)
        self.stock_in(product, 10)
        self.assertEqual(self.ok(self.client.get(f"/products/{product}"))["avg_cost_cents"], 100)

    def test_all_locations_count_towards_the_average(self):
        product = self.make_product()["id"]
        other = self.make_location()["id"]
        self.stock_in(product, 10, unit_cost_cents=100)
        self.stock_in(product, 10, unit_cost_cents=200, location_id=other)
        self.assertEqual(self.ok(self.client.get(f"/products/{product}"))["avg_cost_cents"], 150)


class Summary(ApiTestCase):
    def test_per_location_breakdown(self):
        product = self.make_product()["id"]
        other = self.make_location()["id"]
        self.stock_in(product, 10)
        self.stock_in(product, 4, location_id=other)
        self.ok(self.reserve(product, 3, location_id=other), 201)
        summary = self.ok(self.client.get(f"/products/{product}/stock"))
        self.assertEqual((summary["on_hand"], summary["reserved"], summary["available"]), (14, 3, 11))
        by_id = {row["location_id"]: row for row in summary["locations"]}
        self.assertEqual(by_id[other]["available"], 1)
        self.assertEqual(by_id[other]["warehouse_code"], "MAIN")

    def test_no_stock_yet(self):
        product = self.make_product()["id"]
        summary = self.ok(self.client.get(f"/products/{product}/stock"))
        self.assertEqual((summary["on_hand"], summary["locations"]), (0, []))

    def test_missing_product(self):
        self.assertEqual(self.client.get("/products/999/stock").status, 404)


class Transfers(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()["id"]
        self.other = self.make_location()["id"]
        self.stock_in(self.product, 10, unit_cost_cents=100)

    def transfer(self, quantity=4, source=1, target=None, **extra):
        return self.client.post(
            "/transfers",
            {
                "product_id": self.product,
                "from_location_id": source,
                "to_location_id": self.other if target is None else target,
                "quantity": quantity,
                **extra,
            },
        )

    def levels(self):
        summary = self.ok(self.client.get(f"/products/{self.product}/stock"))
        return {row["location_id"]: row["on_hand"] for row in summary["locations"]}

    def test_moves_stock_and_writes_a_linked_pair(self):
        reply = self.transfer(reference="T-1")
        record = self.ok(reply, 201)
        self.assertEqual(reply.headers["location"], f"/transfers/{record['id']}")
        self.assertEqual(self.levels(), {1: 6, self.other: 4})
        rows = self.ok(self.client.get(f"/products/{self.product}/movements"))["items"]
        linked = [r for r in rows if r["transfer_id"] == record["id"]]
        self.assertEqual(sorted(r["quantity"] for r in linked), [-4, 4])
        self.assertEqual({r["reference"] for r in linked}, {"T-1"})
        self.assertEqual(self.ok(self.client.get(f"/transfers/{record['id']}")), record)

    def test_average_cost_is_unchanged(self):
        self.ok(self.transfer(), 201)
        self.assertEqual(self.ok(self.client.get(f"/products/{self.product}"))["avg_cost_cents"], 100)

    def test_total_is_conserved(self):
        self.ok(self.transfer(), 201)
        self.assertEqual(self.ok(self.client.get(f"/products/{self.product}/stock"))["on_hand"], 10)

    def test_held_units_cannot_leave(self):
        self.ok(self.reserve(self.product, 8), 201)
        reply = self.transfer(quantity=3)
        self.assertEqual(reply.error_code, "insufficient_stock")
        self.assertEqual(self.levels(), {1: 10})

    def test_same_location(self):
        self.assertEqual(self.transfer(target=1).status, 400)

    def test_inactive_target(self):
        self.ok(self.client.post(f"/locations/{self.other}/deactivate"))
        self.assertEqual(self.transfer().error_code, "location_inactive")

    def test_unknown_ids(self):
        self.assertEqual(self.transfer(target=999).status, 404)
        self.assertEqual(self.transfer(source=999).status, 404)
        self.assertEqual(self.client.get("/transfers/999").status, 404)


class Warehouses(ApiTestCase):
    def test_default_warehouse_is_listed(self):
        items = self.ok(self.client.get("/warehouses"))["items"]
        self.assertEqual([w["code"] for w in items], ["MAIN"])

    def test_create_and_read(self):
        reply = self.client.post("/warehouses", {"code": "EAST", "name": "East depot"})
        warehouse = self.ok(reply, 201)
        self.assertEqual(self.ok(self.client.get(f"/warehouses/{warehouse['id']}")), warehouse)

    def test_duplicate_code(self):
        reply = self.client.post("/warehouses", {"code": "main", "name": "Again"})
        self.assertEqual(reply.error_code, "duplicate_code")

    def test_locations_are_per_warehouse(self):
        east = self.ok(self.client.post("/warehouses", {"code": "EAST", "name": "East"}), 201)
        self.make_location("A1", east["id"])
        self.make_location("A1", 1)
        self.assertEqual(self.client.post(f"/warehouses/{east['id']}/locations", {"code": "a1"}).status, 409)
        items = self.ok(self.client.get(f"/warehouses/{east['id']}/locations"))["items"]
        self.assertEqual([i["code"] for i in items], ["A1"])

    def test_unknown_warehouse(self):
        self.assertEqual(self.client.get("/warehouses/999").status, 404)
        self.assertEqual(self.client.get("/warehouses/999/locations").status, 404)
        self.assertEqual(self.client.post("/warehouses/999/locations", {"code": "A"}).status, 404)

    def test_deactivate_needs_an_empty_location(self):
        product = self.make_product()["id"]
        location = self.make_location()["id"]
        self.stock_in(product, 2, location_id=location)
        reply = self.client.post(f"/locations/{location}/deactivate")
        self.assertEqual(reply.error_code, "location_not_empty")
        self.ok(
            self.client.post(
                f"/products/{product}/movements",
                {"kind": "shipment", "quantity": 2, "location_id": location},
            ),
            201,
        )
        record = self.ok(self.client.post(f"/locations/{location}/deactivate"))
        self.assertIs(record["active"], False)

    def test_inactive_location_refuses_new_stock_until_reactivated(self):
        product = self.make_product()["id"]
        location = self.make_location()["id"]
        self.ok(self.client.post(f"/locations/{location}/deactivate"))
        body = {"kind": "receipt", "quantity": 1, "location_id": location}
        reply = self.client.post(f"/products/{product}/movements", body)
        self.assertEqual(reply.error_code, "location_inactive")
        self.assertEqual(self.reserve(product, 1, location_id=location).error_code, "location_inactive")
        self.ok(self.client.post(f"/locations/{location}/activate"))
        self.ok(self.client.post(f"/products/{product}/movements", body), 201)


class Idempotency(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()["id"]
        self.path = f"/products/{self.product}/movements"

    def post(self, key, **body):
        return self.client.post(self.path, body, headers={"Idempotency-Key": key})

    def test_retry_replays_and_does_not_apply_twice(self):
        first = self.post("k1", kind="receipt", quantity=5)
        second = self.post("k1", kind="receipt", quantity=5)
        self.ok(first, 201)
        self.ok(second, 201)
        self.assertEqual(second.headers["idempotency-replayed"], "true")
        self.assertNotIn("idempotency-replayed", first.headers)
        self.assertEqual(first.body, second.body)
        self.assertEqual(self.available(self.product), 5)

    def test_reuse_for_a_different_request(self):
        self.ok(self.post("k1", kind="receipt", quantity=5), 201)
        reply = self.post("k1", kind="receipt", quantity=6)
        self.assertEqual(reply.status, 409)
        self.assertEqual(reply.error_code, "idempotency_key_reused")

    def test_same_key_on_another_path_is_a_different_request(self):
        self.ok(self.post("k1", kind="receipt", quantity=5), 201)
        other = self.make_product("OTHER")["id"]
        reply = self.client.post(
            f"/products/{other}/movements",
            {"kind": "receipt", "quantity": 5},
            headers={"Idempotency-Key": "k1"},
        )
        self.assertEqual(reply.error_code, "idempotency_key_reused")

    def test_failures_are_not_stored(self):
        self.assertEqual(self.post("k1", kind="shipment", quantity=1).status, 409)
        self.stock_in(self.product, 5)
        self.ok(self.post("k1", kind="shipment", quantity=1), 201)

    def test_without_a_key_nothing_is_deduplicated(self):
        self.stock_in(self.product, 5)
        self.stock_in(self.product, 5)
        self.assertEqual(self.available(self.product), 10)

    def test_bad_key(self):
        self.assertEqual(self.post("has space", kind="receipt", quantity=1).status, 400)
        self.assertEqual(self.post("x" * 129, kind="receipt", quantity=1).status, 400)

    def test_works_on_product_creation(self):
        body = {"sku": "NEW", "name": "New", "unit_price_cents": 1}
        headers = {"Idempotency-Key": "k9"}
        first = self.ok(self.client.post("/products", body, headers=headers), 201)
        second = self.ok(self.client.post("/products", body, headers=headers), 201)
        self.assertEqual(first["id"], second["id"])


if __name__ == "__main__":
    unittest.main()
