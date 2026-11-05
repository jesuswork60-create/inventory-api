from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import AuditEntry


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(
        self,
        entity: str,
        entity_id: int,
        action: str,
        detail: dict[str, Any],
        request_id: str | None,
        now: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO audit_log (entity, entity_id, action, detail, request_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity, entity_id, action, json.dumps(detail, sort_keys=True), request_id, now),
        )

    def list(
        self,
        *,
        limit: int,
        cursor: int | None = None,
        entity: str | None = None,
        entity_id: int | None = None,
    ) -> list[AuditEntry]:
        """Newest first."""
        clauses = ["1 = 1"]
        args: list[Any] = []
        if cursor is not None:
            clauses.append("id < ?")
            args.append(cursor)
        if entity:
            clauses.append("entity = ?")
            args.append(entity)
        if entity_id is not None:
            clauses.append("entity_id = ?")
            args.append(entity_id)
        args.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM audit_log WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", args
        )
        return [AuditEntry.from_row(row) for row in rows]


class IdempotencyRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM idempotency_keys WHERE key = ?", (key,)).fetchone()

    def store(self, key: str, fingerprint: str, status: int, body: bytes, now: str) -> None:
        self.conn.execute(
            """
            INSERT INTO idempotency_keys (key, fingerprint, status, body, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, fingerprint, status, body, now),
        )

    def delete_older_than(self, stamp: str) -> int:
        cursor = self.conn.execute("DELETE FROM idempotency_keys WHERE created_at < ?", (stamp,))
        return cursor.rowcount
