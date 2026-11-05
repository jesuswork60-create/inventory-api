"""Declarative request body checks.

A :class:`Schema` maps field names to rules. Loading reports every problem at
once rather than stopping at the first, since an API client usually wants the
whole list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .clock import parse_stamp
from .errors import ValidationFailed

MAX_INT = 2**53  # beyond this a JSON client may lose precision


class Rule(Protocol):
    required: bool

    def check(self, value: Any) -> Any:
        """Return the cleaned value or raise ValueError with a message."""


@dataclass(slots=True)
class Text:
    required: bool = False
    min_length: int = 0
    max_length: int = 500
    pattern: str | None = None
    choices: tuple[str, ...] | None = None
    strip: bool = True

    def check(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        cleaned = value.strip() if self.strip else value
        if len(cleaned) < self.min_length:
            raise ValueError(
                "must not be empty" if self.min_length == 1 else f"must have at least {self.min_length} characters"
            )
        if len(cleaned) > self.max_length:
            raise ValueError(f"must have at most {self.max_length} characters")
        if self.pattern and not re.fullmatch(self.pattern, cleaned):
            raise ValueError("has an invalid format")
        if self.choices and cleaned not in self.choices:
            raise ValueError(f"must be one of: {', '.join(self.choices)}")
        return cleaned


@dataclass(slots=True)
class Integer:
    required: bool = False
    minimum: int = -MAX_INT
    maximum: int = MAX_INT
    nonzero: bool = False

    def check(self, value: Any) -> int:
        # bool is a subclass of int, and true must not silently mean 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("must be a whole number")
        if value < self.minimum:
            raise ValueError(f"must be at least {self.minimum}")
        if value > self.maximum:
            raise ValueError(f"must be at most {self.maximum}")
        if self.nonzero and value == 0:
            raise ValueError("must not be zero")
        return value


@dataclass(slots=True)
class Timestamp:
    required: bool = False

    def check(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("must be an ISO 8601 string")
        try:
            return parse_stamp(value)
        except ValueError as exc:
            raise ValueError(f"is not a valid time: {exc}") from None


@dataclass(slots=True)
class ListOf:
    item: "Schema"
    required: bool = False
    min_items: int = 0
    max_items: int = 500

    def check(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ValueError("must be a list")
        if len(value) < self.min_items:
            raise ValueError(f"must have at least {self.min_items} item(s)")
        if len(value) > self.max_items:
            raise ValueError(f"must have at most {self.max_items} items")
        return value  # items are checked by Schema so that their errors keep a path


@dataclass(slots=True)
class Schema:
    fields: Mapping[str, Rule] = field(default_factory=dict)

    def load(self, data: Any, *, partial: bool = False) -> dict[str, Any]:
        """Validate, returning only the fields supplied.

        With ``partial`` required fields may be absent, as for PATCH. A null value
        is never accepted here: every field either has a value or is left out.
        """
        errors: dict[str, str] = {}
        cleaned = self._collect(data, partial, "", errors)
        if errors:
            raise ValidationFailed(errors)
        return cleaned

    def _collect(self, data: Any, partial: bool, prefix: str, errors: dict[str, str]) -> dict[str, Any]:
        if not isinstance(data, dict):
            errors[prefix.rstrip(".") or "body"] = "must be an object"
            return {}
        cleaned: dict[str, Any] = {}
        for name in data:
            if name not in self.fields:
                errors[f"{prefix}{name}"] = "is not a known field"
        for name, rule in self.fields.items():
            path = f"{prefix}{name}"
            if name not in data:
                if rule.required and not partial:
                    errors[path] = "is required"
                continue
            value = data[name]
            if value is None:
                errors[path] = "must not be null"
                continue
            try:
                checked = rule.check(value)
            except ValueError as exc:
                errors[path] = str(exc)
                continue
            if isinstance(rule, ListOf):
                checked = [
                    rule.item._collect(item, False, f"{path}[{index}].", errors)
                    for index, item in enumerate(checked)
                ]
            cleaned[name] = checked
        return cleaned
