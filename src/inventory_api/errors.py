"""Errors that map directly onto HTTP responses."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details


class BadRequest(ApiError):
    status = 400
    code = "bad_request"


class NotFound(ApiError):
    status = 404
    code = "not_found"


class MethodNotAllowed(ApiError):
    status = 405
    code = "method_not_allowed"

    def __init__(self, message: str, *, allowed: list[str]) -> None:
        super().__init__(message, details={"allowed": allowed})
        self.allowed = allowed


class Conflict(ApiError):
    status = 409
    code = "conflict"


class PreconditionFailed(ApiError):
    status = 412
    code = "version_mismatch"


class PayloadTooLarge(ApiError):
    status = 413
    code = "payload_too_large"


class UnsupportedMediaType(ApiError):
    status = 415
    code = "unsupported_media_type"


class ValidationFailed(ApiError):
    status = 422
    code = "validation_failed"

    def __init__(self, errors: dict[str, str], message: str = "the request body is not valid") -> None:
        super().__init__(message, details=errors)
        self.errors = errors


class PreconditionRequired(ApiError):
    status = 428
    code = "precondition_required"


class ServiceUnavailable(ApiError):
    status = 503
    code = "unavailable"
