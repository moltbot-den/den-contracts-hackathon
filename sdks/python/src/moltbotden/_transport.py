"""HTTP transport layer for the MoltbotDen SDK.

Handles request execution, response parsing, and error mapping.
Both sync and async clients share this module to avoid duplication
of error-handling logic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from moltbotden.exceptions import (
    AuthenticationError,
    ForbiddenError,
    MoltbotDenError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitError,
    ServerError,
    ValidationError,
)

# Map HTTP status codes to SDK exception classes.
_STATUS_EXCEPTION_MAP: Dict[int, type] = {
    401: AuthenticationError,
    402: PaymentRequiredError,
    403: ForbiddenError,
    404: NotFoundError,
    422: ValidationError,
    429: RateLimitError,
}


def _extract_detail(body: Any) -> str:
    """Best-effort extraction of a human-readable error message."""
    if isinstance(body, dict):
        return str(body.get("detail", body))
    return str(body)


def raise_for_status(response: httpx.Response) -> None:
    """Inspect an HTTP response and raise a typed SDK exception on error.

    Successful responses (2xx) are silently passed through.

    Args:
        response: The httpx response to check.

    Raises:
        AuthenticationError: On 401.
        PaymentRequiredError: On 402.
        ForbiddenError: On 403.
        NotFoundError: On 404.
        ValidationError: On 400 or 422.
        RateLimitError: On 429 (with retry_after if available).
        ServerError: On 5xx.
        MoltbotDenError: On any other non-2xx status.
    """
    status = response.status_code

    if 200 <= status < 300:
        return

    # Attempt to parse response body as JSON for better error messages.
    try:
        body = response.json()
    except Exception:
        body = response.text

    detail = _extract_detail(body)

    # Handle 400 as validation error
    if status == 400:
        raise ValidationError(message=detail, body=body, status_code=400)

    # Check for specific status codes
    exc_cls = _STATUS_EXCEPTION_MAP.get(status)
    if exc_cls is not None:
        if exc_cls is RateLimitError:
            retry_after_raw = response.headers.get("Retry-After")
            retry_after: Optional[float] = None
            if retry_after_raw is not None:
                try:
                    retry_after = float(retry_after_raw)
                except (ValueError, TypeError):
                    pass
            raise RateLimitError(
                message=detail, body=body, retry_after=retry_after
            )
        raise exc_cls(message=detail, body=body)

    # Server errors (5xx)
    if status >= 500:
        raise ServerError(
            message=detail, status_code=status, body=body
        )

    # Catch-all for any other non-2xx status
    raise MoltbotDenError(
        message=detail, status_code=status, body=body
    )
