"""Wraps a WSGI environ in something pleasant to read from."""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.parse import parse_qs

from ..clock import parse_stamp
from ..errors import BadRequest, PayloadTooLarge, UnsupportedMediaType

MAX_BODY_BYTES = 1024 * 1024
_TRUE = {"1", "true", "yes"}
_FALSE = {"0", "false", "no"}


class Request:
    def __init__(self, environ: Mapping[str, Any]) -> None:
        self.environ = environ
        self.method: str = environ.get("REQUEST_METHOD", "GET").upper()
        self.path: str = environ.get("PATH_INFO", "/") or "/"
        self.query: dict[str, list[str]] = parse_qs(
            environ.get("QUERY_STRING", ""), keep_blank_values=True
        )
        self.params: dict[str, Any] = {}
        self.request_id: str = ""
        self.services: Any = None  # set per request by the session middleware
        self._body: bytes | None = None

    def header(self, name: str, default: str | None = None) -> str | None:
        key = name.upper().replace("-", "_")
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            return self.environ.get(key) or default
        return self.environ.get(f"HTTP_{key}", default)

    @property
    def body(self) -> bytes:
        if self._body is None:
            self._body = self._read_body()
        return self._body

    def _read_body(self) -> bytes:
        raw_length = self.environ.get("CONTENT_LENGTH") or "0"
        try:
            length = int(raw_length)
        except ValueError:
            raise BadRequest("Content-Length is not a number") from None
        if length < 0:
            raise BadRequest("Content-Length is negative")
        if length > MAX_BODY_BYTES:
            raise PayloadTooLarge(f"the body may be at most {MAX_BODY_BYTES} bytes")
        stream = self.environ.get("wsgi.input")
        return stream.read(length) if stream is not None and length else b""

    def json(self) -> Any:
        content_type = (self.header("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise UnsupportedMediaType("send the body as application/json")
        if not self.body:
            raise BadRequest("the body is empty")
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise BadRequest(f"the body is not valid JSON: {exc}") from None

    def first(self, name: str) -> str | None:
        values = self.query.get(name)
        return values[-1] if values else None

    def query_int(
        self, name: str, default: int | None = None, *, minimum: int | None = None, maximum: int | None = None
    ) -> int | None:
        text = self.first(name)
        if text is None or text == "":
            return default
        try:
            value = int(text)
        except ValueError:
            raise BadRequest(f"query parameter {name} must be a whole number") from None
        if minimum is not None and value < minimum:
            raise BadRequest(f"query parameter {name} must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise BadRequest(f"query parameter {name} must be at most {maximum}")
        return value

    def query_bool(self, name: str, default: bool = False) -> bool:
        text = self.first(name)
        if text is None or text == "":
            return default
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise BadRequest(f"query parameter {name} must be true or false")

    def query_time(self, name: str) -> str | None:
        text = self.first(name)
        if text is None or text == "":
            return None
        try:
            # A '+' in a query string decodes to a space, so restore an offset.
            return parse_stamp(text.replace(" ", "+"))
        except ValueError:
            raise BadRequest(f"query parameter {name} is not a valid time") from None
