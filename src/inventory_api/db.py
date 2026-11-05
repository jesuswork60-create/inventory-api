"""SQLite access: one connection per request, explicit transactions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

BUSY_TIMEOUT_MS = 5000


class Database:
    """Knows where the data lives and how to open it. Holds no open connection."""

    def __init__(self, path: str) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        # isolation_level=None turns off the driver's implicit BEGIN, so the only
        # transactions are the ones opened by transaction() below.
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def enable_wal(self) -> None:
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        finally:
            conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block atomically, joining the open transaction if there is one.

    BEGIN IMMEDIATE takes the write lock up front. Stock checks read and then
    write, and a deferred transaction could fail halfway when upgrading its lock,
    or let two requests pass the same check.
    """
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
