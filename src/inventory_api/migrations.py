"""Numbered schema migrations. Applied ones are recorded and never run twice."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

from .clock import Clock, system_clock, to_stamp
from .db import transaction


class MigrationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "products",
        (
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                sku TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
                reorder_point INTEGER NOT NULL DEFAULT 0 CHECK (reorder_point >= 0),
                avg_cost_cents INTEGER NOT NULL DEFAULT 0 CHECK (avg_cost_cents >= 0),
                version INTEGER NOT NULL DEFAULT 1,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            # A deleted product's SKU may be used again.
            """
            CREATE UNIQUE INDEX products_active_sku
                ON products (sku COLLATE NOCASE) WHERE deleted_at IS NULL
            """,
        ),
    ),
    Migration(
        2,
        "warehouses and stock",
        (
            """
            CREATE TABLE warehouses (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE locations (
                id INTEGER PRIMARY KEY,
                warehouse_id INTEGER NOT NULL REFERENCES warehouses (id),
                code TEXT NOT NULL COLLATE NOCASE,
                name TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE (warehouse_id, code)
            )
            """,
            """
            CREATE TABLE stock_levels (
                product_id INTEGER NOT NULL REFERENCES products (id),
                location_id INTEGER NOT NULL REFERENCES locations (id),
                on_hand INTEGER NOT NULL CHECK (on_hand >= 0),
                PRIMARY KEY (product_id, location_id)
            ) WITHOUT ROWID
            """,
            """
            CREATE TABLE transfers (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products (id),
                from_location_id INTEGER NOT NULL REFERENCES locations (id),
                to_location_id INTEGER NOT NULL REFERENCES locations (id),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                reference TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE movements (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products (id),
                location_id INTEGER NOT NULL REFERENCES locations (id),
                kind TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity <> 0),
                unit_cost_cents INTEGER,
                reference TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                transfer_id INTEGER REFERENCES transfers (id),
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX movements_by_product ON movements (product_id, id)",
            "CREATE INDEX movements_by_time ON movements (created_at)",
            # Every installation starts with somewhere to put stock.
            """
            INSERT INTO warehouses (id, code, name, created_at)
            VALUES (1, 'MAIN', 'Main warehouse', '1970-01-01T00:00:00Z')
            """,
            """
            INSERT INTO locations (id, warehouse_id, code, name, created_at)
            VALUES (1, 1, 'DEFAULT', 'Default location', '1970-01-01T00:00:00Z')
            """,
        ),
    ),
    Migration(
        3,
        "reservations",
        (
            """
            CREATE TABLE reservations (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products (id),
                location_id INTEGER NOT NULL REFERENCES locations (id),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                fulfilled_quantity INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                reference TEXT NOT NULL DEFAULT '',
                expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX reservations_open
                ON reservations (product_id, location_id) WHERE status = 'active'
            """,
        ),
    ),
    Migration(
        4,
        "purchase orders",
        (
            """
            CREATE TABLE purchase_orders (
                id INTEGER PRIMARY KEY,
                supplier TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE purchase_order_lines (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES purchase_orders (id),
                product_id INTEGER NOT NULL REFERENCES products (id),
                quantity_ordered INTEGER NOT NULL CHECK (quantity_ordered > 0),
                quantity_received INTEGER NOT NULL DEFAULT 0,
                unit_cost_cents INTEGER NOT NULL CHECK (unit_cost_cents >= 0),
                UNIQUE (order_id, product_id),
                CHECK (quantity_received <= quantity_ordered)
            )
            """,
            "CREATE INDEX purchase_lines_by_product ON purchase_order_lines (product_id)",
        ),
    ),
    Migration(
        5,
        "audit log and idempotency keys",
        (
            """
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY,
                entity TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '{}',
                request_id TEXT,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX audit_by_entity ON audit_log (entity, entity_id, id)",
            """
            CREATE TABLE idempotency_keys (
                key TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                status INTEGER NOT NULL,
                body BLOB NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
        ),
    ),
)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def applied_versions(conn: sqlite3.Connection) -> list[int]:
    _ensure_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    return [row["version"] for row in rows]


def validate_sequence(migrations: Sequence[Migration]) -> None:
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"migration versions must count up from 1, got {versions}")


def migrate(
    conn: sqlite3.Connection,
    *,
    migrations: Sequence[Migration] = MIGRATIONS,
    target: int | None = None,
    clock: Clock = system_clock,
) -> list[Migration]:
    """Apply what is missing, each migration in its own transaction.

    A failing migration rolls back fully and stops the run, leaving the database
    at the last version that succeeded. A database newer than this code is refused
    rather than risked.
    """
    validate_sequence(migrations)
    applied = set(applied_versions(conn))
    known = {migration.version for migration in migrations}
    unknown = applied - known
    if unknown:
        raise MigrationError(
            f"database has migrations this version does not know: {sorted(unknown)}"
        )
    done: list[Migration] = []
    for migration in migrations:
        if migration.version in applied:
            continue
        if target is not None and migration.version > target:
            break
        try:
            with transaction(conn):
                for statement in migration.statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, to_stamp(clock())),
                )
        except sqlite3.Error as exc:
            raise MigrationError(
                f"migration {migration.version} ({migration.name}) failed: {exc}"
            ) from exc
        done.append(migration)
    return done


def pending(conn: sqlite3.Connection, migrations: Sequence[Migration] = MIGRATIONS) -> list[Migration]:
    applied = set(applied_versions(conn))
    return [migration for migration in migrations if migration.version not in applied]
