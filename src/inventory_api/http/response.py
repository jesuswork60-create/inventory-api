"""Responses, and the small amount of formatting WSGI wants."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any


@dataclass(slots=True)
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def status_line(self) -> str:
        try:
            return f"{self.status} {HTTPStatus(self.status).phrase}"
        except ValueError:
            return f"{self.status} Unknown"

    def header_list(self) -> list[tuple[str, str]]:
        headers = dict(self.headers)
        if self.status != 204:
            headers.setdefault("Content-Length", str(len(self.body)))
        return list(headers.items())


def json_response(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    merged = {"Content-Type": "application/json; charset=utf-8"}
    merged.update(headers or {})
    return Response(status, body, merged)


def no_content(headers: dict[str, str] | None = None) -> Response:
    return Response(204, b"", dict(headers or {}))
