"""URL templates such as ``/products/{id:int}/stock``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..errors import MethodNotAllowed, NotFound

Handler = Callable[[Any], Any]
_PLACEHOLDER = re.compile(r"\{(\w+)(?::(\w+))?\}")
MAX_ID = 2**63 - 1


def _convert_int(text: str) -> int:
    value = int(text)
    if value > MAX_ID:  # SQLite integers stop here; such an id cannot exist
        raise ValueError(text)
    return value


_CONVERTERS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "int": (r"[0-9]+", _convert_int),
    "str": (r"[^/]+", str),
}


@dataclass(slots=True)
class _Route:
    template: str
    pattern: re.Pattern[str]
    converters: dict[str, Callable[[str], Any]]
    handlers: dict[str, Handler]


def _compile(template: str) -> tuple[re.Pattern[str], dict[str, Callable[[str], Any]]]:
    converters: dict[str, Callable[[str], Any]] = {}
    pattern = ""
    position = 0
    for match in _PLACEHOLDER.finditer(template):
        pattern += re.escape(template[position:match.start()])
        name, kind = match[1], match[2] or "str"
        if kind not in _CONVERTERS:
            raise ValueError(f"unknown converter {kind!r} in {template}")
        regex, convert = _CONVERTERS[kind]
        pattern += f"(?P<{name}>{regex})"
        converters[name] = convert
        position = match.end()
    pattern += re.escape(template[position:])
    return re.compile(pattern), converters


class Router:
    def __init__(self) -> None:
        self._routes: list[_Route] = []
        self._by_template: dict[str, _Route] = {}

    def add(self, method: str, template: str, handler: Handler) -> None:
        route = self._by_template.get(template)
        if route is None:
            pattern, converters = _compile(template)
            route = _Route(template, pattern, converters, {})
            self._by_template[template] = route
            self._routes.append(route)
        method = method.upper()
        if method in route.handlers:
            raise ValueError(f"{method} {template} is already registered")
        route.handlers[method] = handler

    def get(self, template: str, handler: Handler) -> None:
        self.add("GET", template, handler)

    def post(self, template: str, handler: Handler) -> None:
        self.add("POST", template, handler)

    def patch(self, template: str, handler: Handler) -> None:
        self.add("PATCH", template, handler)

    def delete(self, template: str, handler: Handler) -> None:
        self.add("DELETE", template, handler)

    def resolve(self, method: str, path: str) -> tuple[Handler, dict[str, Any]]:
        """Find the handler for a request, or raise 404 or 405.

        Routes are tried in registration order; a literal segment should therefore
        be registered before a placeholder that could also match it. HEAD uses the
        GET handler, and the WSGI layer drops the body.
        """
        for route in self._routes:
            match = route.pattern.fullmatch(path)
            if match is None:
                continue
            try:
                params = {
                    name: route.converters[name](text) for name, text in match.groupdict().items()
                }
            except ValueError:
                continue
            handler = route.handlers.get(method)
            if handler is None and method == "HEAD":
                handler = route.handlers.get("GET")
            if handler is None:
                allowed = sorted(route.handlers)
                raise MethodNotAllowed(f"{method} is not supported here", allowed=allowed)
            return handler, params
        raise NotFound(f"nothing at {path}")
