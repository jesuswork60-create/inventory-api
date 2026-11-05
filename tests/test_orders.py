"""Reservations, purchase orders and reports."""

import unittest

from support import ApiTestCase


class Reservations(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()["id"]
        self.stock_in(self.product, 10)

    def test_holding_reduces_available_not_on_hand(self):
        reply = self.reserve(self.product, 4, reference="order-1")
        reservation = self.ok(reply, 201)
        self.assertEqual(reply.headers["location"], f"/reservations/{reservation['id']}")
        self.assertEqual(reservation["status"], "active")
        self.assertEqual(reservation["open_quantity"], 4)
        summary = self.ok(self.client.get(f"/products/{self.product}/stock"))
        self.assertEqual((summary["on_hand"], summary["available"]), (10, 6))

    def test_cannot_hold_more_than_available(self):
        self.ok(self.reserve(self.product, 8), 201)
        reply = self.reserve(self.product, 3)
        self.assertEqual(reply.error_code, "insufficient_stock")

    def test_unknown_product_or_location(self):
        self.assertEqual(self.reserve(999, 1).status, 404)
        self.assertEqual(self.reserve(self.product, 1, location_id=999).status, 404)

    def test_full_fulfillment_ships_stock(self):
        reservation = self.ok(self.reserve(self.product, 4), 201)
        done = self.ok(self.client.post(f"/reservations/{reservation['id']}/fulfill"))
        self.assertEqual(done["status"], "fulfilled")
        self.assertEqual(done["open_quantity"], 0)
        summary = self.ok(self.client.get(f"/products/{self.product}/stock"))
        self.assertEqual((summary["on_hand"], summary["reserved"]), (6, 0))
        rows = self.ok(self.client.get(f"/products/{self.product}/movements?kind=fulfillment"))["items"]
        self.assertEqual(rows[0]["quantity"], -4)
        self.assertEqual(rows[0]["reference"], f"reservation-{reservation['id']}")

    def test_partial_fulfillment_keeps_the_rest_held(self):
        reservation = self.ok(self.reserve(self.product, 4), 201)
        path = f"/reservations/{reservation['id']}/fulfill"
        partial = self.ok(self.client.post(path, {"quantity": 1}))
        self.assertEqual((partial["status"], partial["open_quantity"]), ("active", 3))
        summary = self.ok(self.client.get(f"/products/{self.product}/stock"))
        self.assertEqual((summary["on_hand"], summary["reserved"], summary["available"]), (9, 3, 6))
        rest = self.ok(self.client.post(path))
        self.assertEqual(rest["status"], "fulfilled")

    def test_over_fulfillment(self):
        reservation = self.ok(self.reserve(self.product, 2), 201)
        reply = self.client.post(f"/reservations/{reservation['id']}/fulfill", {"quantity": 3})
        self.assertEqual(reply.error_code, "over_fulfillment")

    def test_fulfilling_after_a_count_shortage_is_refused_and_keeps_the_hold(self):
        reservation = self.ok(self.reserve(self.product, 8), 201)
        self.ok(
            self.client.post(
                f"/products/{self.product}/movements", {"kind": "adjustment", "quantity": -5}
            ),
            201,
        )
        path = f"/reservations/{reservation['id']}/fulfill"
        reply = self.client.post(path)
        self.assertEqual(reply.error_code, "insufficient_stock")
        self.ok(self.client.post(path, {"quantity": 5}))

    def test_cancel_releases_the_hold(self):
        reservation = self.ok(self.reserve(self.product, 4), 201)
        self.assertEqual(self.ok(self.client.post(f"/reservations/{reservation['id']}/cancel"))["status"], "cancelled")
        self.assertEqual(self.available(self.product), 10)

    def test_closed_reservations_cannot_change_again(self):
        reservation = self.ok(self.reserve(self.product, 1), 201)
        base = f"/reservations/{reservation['id']}"
        self.ok(self.client.post(f"{base}/cancel"))
        self.assertEqual(self.client.post(f"{base}/cancel").error_code, "reservation_closed")
        self.assertEqual(self.client.post(f"{base}/fulfill").error_code, "reservation_closed")

    def test_expired_hold_stops_counting_before_any_sweep(self):
        self.ok(self.reserve(self.product, 8, ttl_seconds=60), 201)
        self.assertEqual(self.available(self.product), 2)
        self.clock.advance(seconds=59)
        self.assertEqual(self.available(self.product), 2)
        self.clock.advance(seconds=1)
        self.assertEqual(self.available(self.product), 10)

    def test_expired_hold_cannot_be_fulfilled(self):
        reservation = self.ok(self.reserve(self.product, 8, ttl_seconds=60), 201)
        self.clock.advance(seconds=60)
        reply = self.client.post(f"/reservations/{reservation['id']}/fulfill")
        self.assertEqual(reply.error_code, "reservation_expired")

    def test_expired_hold_can_still_be_cancelled_before_the_sweep(self):
        reservation = self.ok(self.reserve(self.product, 8, ttl_seconds=60), 201)
        self.clock.advance(seconds=60)
        self.ok(self.client.post(f"/reservations/{reservation['id']}/cancel"))

    def test_sweep_closes_overdue_only(self):
        due = self.ok(self.reserve(self.product, 1, ttl_seconds=60), 201)
        later = self.ok(self.reserve(self.product, 1, ttl_seconds=600), 201)
        forever = self.ok(self.reserve(self.product, 1), 201)
        self.clock.advance(seconds=61)
        self.assertEqual(self.ok(self.client.post("/reservations/expire"))["expired"], 1)
        statuses = {
            r["id"]: r["status"] for r in self.ok(self.client.get("/reservations"))["items"]
        }
        self.assertEqual(statuses[due["id"]], "expired")
        self.assertEqual(statuses[later["id"]], "active")
        self.assertEqual(statuses[forever["id"]], "active")
        self.assertEqual(self.ok(self.client.post("/reservations/expire"))["expired"], 0)

    def test_sweep_does_not_collide_with_the_id_route(self):
        self.assertEqual(self.client.get("/reservations/expire").status, 405)

    def test_ttl_limits(self):
        self.assertEqual(self.reserve(self.product, 1, ttl_seconds=0).status, 422)
        self.assertEqual(self.reserve(self.product, 1, ttl_seconds=10**9).status, 422)

    def test_list_filters(self):
        other = self.make_product("OTHER")["id"]
        self.stock_in(other, 1)
        kept = self.ok(self.reserve(other, 1), 201)
        gone = self.ok(self.reserve(self.product, 1), 201)
        self.ok(self.client.post(f"/reservations/{gone['id']}/cancel"))
        items = self.ok(self.client.get("/reservations?status=active"))["items"]
        self.assertEqual([r["id"] for r in items], [kept["id"]])
        items = self.ok(self.client.get(f"/reservations?product_id={self.product}"))["items"]
        self.assertEqual([r["id"] for r in items], [gone["id"]])
        self.assertEqual(self.client.get("/reservations?status=lost").status, 400)
        self.assertEqual(self.client.get("/reservations/999").status, 404)

    def test_idempotent_fulfill_ships_once(self):
        reservation = self.ok(self.reserve(self.product, 4), 201)
        path = f"/reservations/{reservation['id']}/fulfill"
        headers = {"Idempotency-Key": "ship-1"}
        self.ok(self.client.post(path, {"quantity": 1}, headers=headers))
        self.ok(self.client.post(path, {"quantity": 1}, headers=headers))
        self.assertEqual(self.ok(self.client.get(f"/products/{self.product}/stock"))["on_hand"], 9)


class PurchaseOrders(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.make_product("ONE")["id"]
        self.second = self.make_product("TWO")["id"]

    def make_order(self, **values):
        body = {
            "supplier": "ACME Supplies",
            "lines": [
                {"product_id": self.first, "quantity": 10, "unit_cost_cents": 100},
                {"product_id": self.second, "quantity": 5, "unit_cost_cents": 300},
            ],
            **values,
        }
        return self.ok(self.client.post("/purchase-orders", body), 201)

    def submitted(self):
        order = self.make_order()
        return self.ok(self.client.post(f"/purchase-orders/{order['id']}/submit"))

    def receive(self, order, *quantities, **extra):
        lines = [
            {"line_id": line["id"], "quantity": quantity}
            for line, quantity in zip(order["lines"], quantities)
            if quantity
        ]
        return self.client.post(f"/purchase-orders/{order['id']}/receive", {"lines": lines, **extra})

    def on_hand(self, product):
        return self.ok(self.client.get(f"/products/{product}/stock"))["on_hand"]

    def test_new_order_is_a_draft(self):
        order = self.make_order(note="rush")
        self.assertEqual(order["status"], "draft")
        self.assertEqual(order["note"], "rush")
        self.assertEqual(order["lines"][0]["outstanding"], 10)
        self.assertEqual(self.ok(self.client.get(f"/purchase-orders/{order['id']}")), order)

    def test_validation(self):
        bad = self.client.post(
            "/purchase-orders",
            {"supplier": "x", "lines": [{"product_id": self.first, "quantity": 0, "unit_cost_cents": 1}]},
        )
        self.assertEqual(bad.status, 422)
        self.assertIn("lines[0].quantity", bad.json["error"]["details"])
        self.assertEqual(self.client.post("/purchase-orders", {"supplier": "x", "lines": []}).status, 422)

    def test_duplicate_product_on_an_order(self):
        line = {"product_id": self.first, "quantity": 1, "unit_cost_cents": 1}
        self.assertEqual(self.client.post("/purchase-orders", {"supplier": "x", "lines": [line, line]}).status, 400)

    def test_unknown_product_leaves_no_order_behind(self):
        line = {"product_id": 999, "quantity": 1, "unit_cost_cents": 1}
        self.assertEqual(self.client.post("/purchase-orders", {"supplier": "x", "lines": [line]}).status, 404)
        self.assertEqual(self.ok(self.client.get("/purchase-orders"))["items"], [])

    def test_draft_cannot_receive(self):
        order = self.make_order()
        self.assertEqual(self.receive(order, 1).error_code, "invalid_order_state")

    def test_partial_then_complete(self):
        order = self.submitted()
        partial = self.ok(self.receive(order, 4))
        self.assertEqual(partial["status"], "partially_received")
        self.assertEqual(partial["lines"][0]["outstanding"], 6)
        self.assertEqual(self.on_hand(self.first), 4)
        done = self.ok(self.receive(order, 6, 5))
        self.assertEqual(done["status"], "received")
        self.assertEqual((self.on_hand(self.first), self.on_hand(self.second)), (10, 5))

    def test_receipt_sets_the_average_cost(self):
        order = self.submitted()
        self.ok(self.receive(order, 10))
        self.assertEqual(self.ok(self.client.get(f"/products/{self.first}"))["avg_cost_cents"], 100)

    def test_movement_is_labelled(self):
        order = self.submitted()
        self.ok(self.receive(order, 1))
        rows = self.ok(self.client.get(f"/products/{self.first}/movements?kind=po_receipt"))["items"]
        self.assertEqual(rows[0]["reference"], f"po-{order['id']}")
        self.assertEqual(rows[0]["unit_cost_cents"], 100)

    def test_over_receipt_rolls_back_the_whole_delivery(self):
        order = self.submitted()
        reply = self.receive(order, 3, 6)
        self.assertEqual(reply.error_code, "over_receipt")
        self.assertEqual(self.on_hand(self.first), 0)
        fresh = self.ok(self.client.get(f"/purchase-orders/{order['id']}"))
        self.assertEqual(fresh["lines"][0]["quantity_received"], 0)

    def test_delivery_format_errors(self):
        order = self.submitted()
        base = f"/purchase-orders/{order['id']}/receive"
        line = order["lines"][0]["id"]
        twice = {"lines": [{"line_id": line, "quantity": 1}, {"line_id": line, "quantity": 1}]}
        self.assertEqual(self.client.post(base, twice).status, 400)
        self.assertEqual(self.client.post(base, {"lines": [{"line_id": 999, "quantity": 1}]}).status, 404)

    def test_receiving_into_a_chosen_location(self):
        location = self.make_location()["id"]
        order = self.submitted()
        self.ok(self.receive(order, 2, location_id=location))
        summary = self.ok(self.client.get(f"/products/{self.first}/stock"))
        self.assertEqual(summary["locations"][0]["location_id"], location)

    def test_receiving_into_an_inactive_location(self):
        location = self.make_location()["id"]
        self.ok(self.client.post(f"/locations/{location}/deactivate"))
        order = self.submitted()
        self.assertEqual(self.receive(order, 2, location_id=location).error_code, "location_inactive")

    def test_state_changes(self):
        order = self.make_order()
        base = f"/purchase-orders/{order['id']}"
        self.assertEqual(self.client.post(f"{base}/close").error_code, "invalid_order_state")
        self.assertEqual(self.ok(self.client.post(f"{base}/submit"))["status"], "ordered")
        self.assertEqual(self.client.post(f"{base}/submit").error_code, "invalid_order_state")
        self.assertEqual(self.ok(self.client.post(f"{base}/cancel"))["status"], "cancelled")
        self.assertEqual(self.client.post(f"{base}/cancel").error_code, "invalid_order_state")

    def test_part_received_order_is_closed_not_cancelled(self):
        order = self.submitted()
        base = f"/purchase-orders/{order['id']}"
        self.ok(self.receive(order, 1))
        self.assertEqual(self.client.post(f"{base}/cancel").error_code, "invalid_order_state")
        self.assertEqual(self.ok(self.client.post(f"{base}/close"))["status"], "closed")
        self.assertEqual(self.receive(order, 1).error_code, "invalid_order_state")

    def test_listing_by_status(self):
        draft = self.make_order()
        ordered = self.submitted()
        items = self.ok(self.client.get("/purchase-orders?status=ordered"))["items"]
        self.assertEqual([o["id"] for o in items], [ordered["id"]])
        self.assertEqual(len(self.ok(self.client.get("/purchase-orders"))["items"]), 2)
        self.assertEqual(self.client.get("/purchase-orders?status=lost").status, 400)
        self.assertEqual(self.client.get(f"/purchase-orders/{draft['id'] + 99}").status, 404)

    def test_idempotent_delivery_lands_once(self):
        order = self.submitted()
        lines = [{"line_id": order["lines"][0]["id"], "quantity": 2}]
        headers = {"Idempotency-Key": "delivery-1"}
        path = f"/purchase-orders/{order['id']}/receive"
        self.ok(self.client.post(path, {"lines": lines}, headers=headers))
        self.ok(self.client.post(path, {"lines": lines}, headers=headers))
        self.assertEqual(self.on_hand(self.first), 2)


class LowStock(ApiTestCase):
    def report(self, query=""):
        return self.ok(self.client.get(f"/reports/low-stock{query}"))["items"]

    def test_lists_products_at_or_below_their_point(self):
        low = self.make_product("LOW", reorder_point=5)["id"]
        edge = self.make_product("EDGE", reorder_point=5)["id"]
        fine = self.make_product("FINE", reorder_point=5)["id"]
        self.stock_in(low, 2)
        self.stock_in(edge, 5)
        self.stock_in(fine, 6)
        rows = self.report()
        self.assertEqual([r["sku"] for r in rows], ["LOW", "EDGE"])
        self.assertEqual(rows[0]["shortfall"], 3)
        self.assertEqual(rows[1]["shortfall"], 0)

    def test_reservations_count_against_availability(self):
        product = self.make_product("P", reorder_point=5)["id"]
        self.stock_in(product, 8)
        self.assertEqual(self.report(), [])
        self.ok(self.reserve(product, 4), 201)
        self.assertEqual(self.report()[0]["available"], 4)

    def test_untracked_products_are_ignored(self):
        self.make_product("P", reorder_point=0)
        self.assertEqual(self.report(), [])

    def test_deleted_products_are_ignored(self):
        product = self.make_product("P", reorder_point=5)["id"]
        self.ok(self.client.delete(f"/products/{product}"), 204)
        self.assertEqual(self.report(), [])

    def test_incoming_stock_is_shown_and_can_exclude(self):
        product = self.make_product("P", reorder_point=5)["id"]
        body = {"supplier": "x", "lines": [{"product_id": product, "quantity": 10, "unit_cost_cents": 1}]}
        order = self.ok(self.client.post("/purchase-orders", body), 201)
        # A draft has not been ordered yet, so it is not counted.
        self.assertEqual(self.report()[0]["incoming"], 0)
        self.ok(self.client.post(f"/purchase-orders/{order['id']}/submit"))
        self.assertEqual(self.report()[0]["incoming"], 10)
        self.assertEqual(self.report("?exclude_covered=true"), [])


class Valuation(ApiTestCase):
    def test_totals(self):
        one = self.make_product("ONE")["id"]
        two = self.make_product("TWO")["id"]
        self.stock_in(one, 10, unit_cost_cents=100)
        self.stock_in(two, 1, unit_cost_cents=5000)
        report = self.ok(self.client.get("/reports/valuation"))
        self.assertEqual(report["total_value_cents"], 6000)
        self.assertEqual([i["sku"] for i in report["items"]], ["TWO", "ONE"])

    def test_scoped_to_a_warehouse(self):
        east = self.ok(self.client.post("/warehouses", {"code": "EAST", "name": "East"}), 201)["id"]
        location = self.make_location("E1", east)["id"]
        product = self.make_product()["id"]
        self.stock_in(product, 10, unit_cost_cents=100)
        self.stock_in(product, 5, unit_cost_cents=100, location_id=location)
        report = self.ok(self.client.get(f"/reports/valuation?warehouse_id={east}"))
        self.assertEqual(report["total_value_cents"], 500)
        self.assertEqual(self.client.get("/reports/valuation?warehouse_id=999").status, 404)

    def test_empty(self):
        self.assertEqual(self.ok(self.client.get("/reports/valuation"))["total_value_cents"], 0)


class MovementReport(ApiTestCase):
    def test_grouped_by_kind_within_a_window(self):
        product = self.make_product()["id"]
        self.stock_in(product, 10)
        self.clock.advance(days=2)
        self.ok(self.client.post(f"/products/{product}/movements", {"kind": "shipment", "quantity": 3}), 201)
        self.ok(self.client.post(f"/products/{product}/movements", {"kind": "shipment", "quantity": 2}), 201)
        report = self.ok(self.client.get("/reports/movements"))
        by_kind = {row["kind"]: row for row in report["kinds"]}
        self.assertEqual(by_kind["shipment"]["movements"], 2)
        self.assertEqual(by_kind["shipment"]["net_quantity"], -5)
        report = self.ok(self.client.get("/reports/movements?since=2024-03-02"))
        self.assertEqual([row["kind"] for row in report["kinds"]], ["shipment"])
        report = self.ok(self.client.get("/reports/movements?until=2024-03-02"))
        self.assertEqual([row["kind"] for row in report["kinds"]], ["receipt"])


if __name__ == "__main__":
    unittest.main()
