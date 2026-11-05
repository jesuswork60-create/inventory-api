# inventory-api

An HTTP API for keeping track of what is in stock: products, stock movements,
reservations, transfers between locations, and purchase orders. It is plain WSGI
on top of SQLite and uses only the Python standard library, so there is nothing
to install beyond Python itself.

The main idea is that stock levels are never edited directly. Every change is a
row in a movement ledger, written in the same transaction as the level it
changes, so the history always adds up to the current figure.

## Install

Python 3.11 or newer.

    pip install .

Or run it from a checkout with `PYTHONPATH=src python3 -m inventory_api ...`.

## Usage

    inventory-api --db stock.db seed      # optional: four sample products
    inventory-api --db stock.db serve --port 8000

`serve` applies any pending migrations first. `--port 0` picks a free port and
prints it. `migrate --status` lists what would be applied.

    # create a product
    curl -s localhost:8000/products -H 'Content-Type: application/json' \
      -d '{"sku": "TEA-1", "name": "Green tea", "unit_price_cents": 450, "reorder_point": 5}'

    # goods arrive; suppose the new product got id 5
    curl -s localhost:8000/products/5/movements -H 'Content-Type: application/json' \
      -d '{"kind": "receipt", "quantity": 20, "unit_cost_cents": 210}'

    # hold 6 for an order for an hour, then ship them
    curl -s localhost:8000/reservations -H 'Content-Type: application/json' \
      -d '{"product_id": 5, "quantity": 6, "ttl_seconds": 3600}'
    curl -s -X POST localhost:8000/reservations/1/fulfill

    curl -s localhost:8000/products/5/stock

## The API

| Path | Methods | Notes |
| --- | --- | --- |
| `/health` | GET | 503 if migrations are pending |
| `/products` | GET, POST | `search`, `include_deleted`, `limit`, `cursor` |
| `/products/{id}` | GET, PATCH, DELETE | PATCH needs `If-Match`; DELETE is a soft delete |
| `/products/{id}/stock` | GET | per-location on hand, reserved, available |
| `/products/{id}/movements` | GET, POST | `kind`, `location_id`, `since`, `until` |
| `/transfers`, `/transfers/{id}` | POST, GET | move units between two locations |
| `/warehouses`, `/warehouses/{id}` | GET, POST | |
| `/warehouses/{id}/locations` | GET, POST | |
| `/locations/{id}` | GET | also `POST .../activate`, `POST .../deactivate` |
| `/reservations`, `/reservations/{id}` | GET, POST | `status`, `product_id` |
| `/reservations/{id}/fulfill`, `/cancel` | POST | fulfil takes an optional `quantity` |
| `/reservations/expire` | POST | closes overdue holds, up to 1000 per call |
| `/purchase-orders`, `/purchase-orders/{id}` | GET, POST | |
| `/purchase-orders/{id}/submit`, `/cancel`, `/close`, `/receive` | POST | |
| `/reports/low-stock` | GET | `exclude_covered=true` drops what open orders would cover |
| `/reports/valuation` | GET | `warehouse_id` |
| `/reports/movements` | GET | totals by kind; `since`, `until` |
| `/audit` | GET | `entity`, `entity_id` |

Some behaviour worth knowing:

- **Movements.** Clients post `receipt`, `return`, `shipment`, `scrap` or a signed
  `adjustment`. Shipments may not exceed what is available, meaning on hand minus
  active reservations. An adjustment records a physical count, so it may cut into
  reservations and leave available negative, but it can't take on hand below zero.
- **Reservations.** A hold past its `expires_at` stops counting immediately. The
  sweep only tidies its status.
- **Cost.** `avg_cost_cents` is a weighted average per product, across all
  locations, recalculated when stock arrives with a cost. Halves round up.
- **Optimistic locking.** `GET /products/{id}` returns an `ETag`. PATCH requires
  `If-Match`, either that value or `*`. A stale version gets 412, none at all 428.
- **Idempotent POST.** Send an `Idempotency-Key` header when creating products,
  movements, transfers, reservations and purchase orders, or when fulfilling or
  receiving. A retry gets the stored response with `Idempotency-Replayed: true`.
  Reusing a key for a different request gets 409. Failures aren't stored.
- **Errors** look like `{"error": {"code", "message", "request_id", "details"}}`.

Paged lists return `{"items": [...], "next_cursor": ...}`. Pass the cursor back
unchanged until it comes back `null`.

## Layout

`http/` is the small web layer (router, request, response, middleware).
`routes/` turns requests into service calls, `services/` holds the rules,
`repositories/` holds all the SQL, and `migrations.py` the schema.

## Tests

    PYTHONPATH=src python3 -m unittest discover -s tests

Most tests call the WSGI application directly with a hand-moved clock. One starts
a real server on a port chosen by the OS.

## Known limitations

- There is no authentication or authorisation of any kind. Keep it off public
  networks.
- `serve` uses `wsgiref`, which is fine for trying things out and small internal
  use but is not a hardened server. `Application` is a normal WSGI callable, so
  another server can host it.
- One SQLite file means one writer at a time. A write that waits more than five
  seconds gets a 503.
- Timestamps have one-second precision, and `since`/`until` compare them as text.
- Migrations only go forward.
- Purchase order lines have no destination; the location is chosen per delivery.
- Idempotency keys are kept forever. Nothing currently deletes old ones.
- Average cost is per product rather than per location, and scrap or shipments
  never change it.
- A `return` carries no cost and does not refer back to the shipment it reverses.
