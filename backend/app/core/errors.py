"""Domain exceptions and their HTTP mapping.

Service and analytics code raise these; the API layer converts them into a
structured JSON error body ``{"error": {"code", "message", "details"}}``.
Stack traces are logged server-side and never returned to the client.
"""

from __future__ import annotations

from typing import Any


class NexisError(Exception):
    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(NexisError):
    status_code = 404
    code = "not_found"


class ValidationFailed(NexisError):
    status_code = 422
    code = "validation_failed"


class InsufficientDataError(NexisError):
    """Raised when a calculation does not have enough observations to be meaningful."""

    status_code = 422
    code = "insufficient_data"


class ConfigurationError(NexisError):
    status_code = 422
    code = "invalid_configuration"


class ProviderError(NexisError):
    status_code = 502
    code = "provider_unavailable"


class ConflictError(NexisError):
    status_code = 409
    code = "conflict"


class ModelError(NexisError):
    status_code = 500
    code = "model_failure"
