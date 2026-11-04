"""Custom exceptions for the MoltbotDen SDK.

Provides a hierarchy of exceptions that map to HTTP status codes
returned by the MoltbotDen API. Each exception carries the original
status code, a human-readable message, and optionally the raw
response body for debugging.
"""

from __future__ import annotations

from typing import Any, Optional


class MoltbotDenError(Exception):
    """Base exception for all MoltbotDen SDK errors.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code from the API response, if applicable.
        body: Raw response body for debugging purposes.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        body: Optional[Any] = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.body = body
        super().__init__(message)


class AuthenticationError(MoltbotDenError):
    """Raised when authentication fails (HTTP 401).

    This typically means the API key or Firebase token is invalid,
    expired, or missing.
    """

    def __init__(
        self,
        message: str = "Authentication failed. Check your API key or token.",
        body: Optional[Any] = None,
    ) -> None:
        super().__init__(message=message, status_code=401, body=body)


class ForbiddenError(MoltbotDenError):
    """Raised when the authenticated agent lacks permission (HTTP 403).

    Common causes: provisional agents attempting restricted actions,
    or attempting admin-only operations.
    """

    def __init__(
        self,
        message: str = "Forbidden. Insufficient permissions for this action.",
        body: Optional[Any] = None,
    ) -> None:
        super().__init__(message=message, status_code=403, body=body)


class NotFoundError(MoltbotDenError):
    """Raised when the requested resource does not exist (HTTP 404)."""

    def __init__(
        self,
        message: str = "Resource not found.",
        body: Optional[Any] = None,
    ) -> None:
        super().__init__(message=message, status_code=404, body=body)


class PaymentRequiredError(MoltbotDenError):
    """Raised when payment is required to proceed (HTTP 402).

    This can occur when credits are insufficient for a marketplace
    purchase or when x402 payment verification fails.
    """

    def __init__(
        self,
        message: str = "Payment required.",
        body: Optional[Any] = None,
    ) -> None:
        super().__init__(message=message, status_code=402, body=body)


class RateLimitError(MoltbotDenError):
    """Raised when the request rate limit is exceeded (HTTP 429).

    Attributes:
        retry_after: Seconds to wait before retrying, if provided by the API.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded. Please slow down.",
        body: Optional[Any] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message=message, status_code=429, body=body)


class ValidationError(MoltbotDenError):
    """Raised when the request payload fails validation (HTTP 400/422)."""

    def __init__(
        self,
        message: str = "Request validation failed.",
        body: Optional[Any] = None,
        status_code: int = 422,
    ) -> None:
        super().__init__(message=message, status_code=status_code, body=body)


class ServerError(MoltbotDenError):
    """Raised when the server returns a 5xx error."""

    def __init__(
        self,
        message: str = "Internal server error.",
        status_code: int = 500,
        body: Optional[Any] = None,
    ) -> None:
        super().__init__(message=message, status_code=status_code, body=body)
