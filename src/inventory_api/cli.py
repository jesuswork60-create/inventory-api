"""``inventory-api serve | migrate | seed``."""

from __future__ import annotations

import argparse
import logging
import socketserver
import sys
from typing import Sequence
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from . import __version__
from .app import Application
from .db import Database
from .migrations import MigrationError, migrate, pending
from .seed import seed


class ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    """The application writes its own access log."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def create_server(database: Database, host: str, port: int):
    """Build, but do not start, a server. Port 0 picks any free port."""
    return make_server(
        host,
        port,
        Application(database),
        server_class=ThreadingWSGIServer,
        handler_class=QuietHandler,
    )


def _command_migrate(args: argparse.Namespace) -> int:
    conn = Database(args.db).connect()
    try:
        if args.status:
            waiting = pending(conn)
            for migration in waiting:
                print(f"pending {migration.version} {migration.name}")
            if not waiting:
                print("up to date")
            return 0
        done = migrate(conn, target=args.target)
        for migration in done:
            print(f"applied {migration.version} {migration.name}")
        if not done:
            print("nothing to do")
    finally:
        conn.close()
    return 0


def _command_seed(args: argparse.Namespace) -> int:
    conn = Database(args.db).connect()
    try:
        migrate(conn)
        count = seed(conn)
    finally:
        conn.close()
    print(f"added {count} sample products" if count else "catalogue is not empty; nothing added")
    return 0


def _command_serve(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    database = Database(args.db)
    conn = database.connect()
    try:
        migrate(conn)
    finally:
        conn.close()
    database.enable_wal()
    with create_server(database, args.host, args.port) as server:
        print(f"listening on http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inventory-api", description="A small inventory HTTP API.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", default="inventory.db", help="SQLite file (default: %(default)s)")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the HTTP server, migrating first")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000, help="0 picks a free port")
    serve.set_defaults(run=_command_serve)

    migration = commands.add_parser("migrate", help="bring the schema up to date")
    migration.add_argument("--status", action="store_true", help="list pending migrations only")
    migration.add_argument("--target", type=int, help="stop after this version")
    migration.set_defaults(run=_command_migrate)

    sample = commands.add_parser("seed", help="add sample products to an empty catalogue")
    sample.set_defaults(run=_command_seed)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except MigrationError as exc:
        print(f"inventory-api: {exc}", file=sys.stderr)
        return 1
