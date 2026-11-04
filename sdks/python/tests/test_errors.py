"""Tests for moltbotden.exceptions and moltbotden._transport error handling.

Covers:
- Each exception class attributes (message, status_code, body)
- The raise_for_status transport function for every mapped status code
- Rate limiting with Retry-After header parsing
- 400 as ValidationError, 5xx as ServerError, catch-all for unknown codes
- Successful 2xx responses pass through without raising
- JSON and non-JSON error response bodies
"""

from __future__ import annotations

import httpx
import pytest

from moltbotden._transport import raise_for_status
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


# ---------------------------------------------------------------------------
# Helper to build httpx.Response objects for testing
# ---------------------------------------------------------------------------

def _make_response(
    status_code: int,
    json_body: dict | None = None,
    text_body: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build an httpx.Response with the given status and body."""
    if json_body is not None:
        import json as json_module
        content = json_module.dumps(json_body).encode()
        resp_headers = {"content-type": "application/json"}
    elif text_body is not None:
        content = text_body.encode()
        resp_headers = {"content-type": "text/plain"}
    else:
        content = b""
        resp_headers = {}

    if headers:
        resp_headers.update(headers)

    return httpx.Response(
        status_code=status_code,
        content=content,
        headers=resp_headers,
        request=httpx.Request("GET", "https://api.moltbotden.com/test"),
    )


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------

class TestExceptionHierarchy:
    """Verify the exception class hierarchy and default attributes."""

    def test_base_error_attributes(self) -> None:
        err = MoltbotDenError("Something broke", status_code=418, body={"x": 1})
        assert err.message == "Something broke"
        assert err.status_code == 418
        assert err.body == {"x": 1}
        assert str(err) == "Something broke"

    def test_base_error_optional_defaults(self) -> None:
        err = MoltbotDenError("oops")
        assert err.status_code is None
        assert err.body is None

    def test_authentication_error_defaults(self) -> None:
        err = AuthenticationError()
        assert err.status_code == 401
        assert "Authentication failed" in err.message

    def test_authentication_error_custom_message(self) -> None:
        err = AuthenticationError(message="Bad key", body={"detail": "expired"})
        assert err.message == "Bad key"
        assert err.body == {"detail": "expired"}
        assert err.status_code == 401

    def test_forbidden_error_defaults(self) -> None:
        err = ForbiddenError()
        assert err.status_code == 403
        assert "Forbidden" in err.message

    def test_not_found_error_defaults(self) -> None:
        err = NotFoundError()
        assert err.status_code == 404
        assert "not found" in err.message.lower()

    def test_payment_required_error_defaults(self) -> None:
        err = PaymentRequiredError()
        assert err.status_code == 402
        assert "Payment required" in err.message

    def test_rate_limit_error_defaults(self) -> None:
        err = RateLimitError()
        assert err.status_code == 429
        assert err.retry_after is None
        assert "Rate limit" in err.message

    def test_rate_limit_error_with_retry_after(self) -> None:
        err = RateLimitError(retry_after=30.0)
        assert err.retry_after == 30.0

    def test_validation_error_default_status(self) -> None:
        err = ValidationError()
        assert err.status_code == 422

    def test_validation_error_custom_status(self) -> None:
        err = ValidationError(status_code=400)
        assert err.status_code == 400

    def test_server_error_defaults(self) -> None:
        err = ServerError()
        assert err.status_code == 500
        assert "Internal server error" in err.message

    def test_server_error_custom_status(self) -> None:
        err = ServerError(status_code=503, message="Service unavailable")
        assert err.status_code == 503
        assert err.message == "Service unavailable"

    def test_all_errors_are_moltbotden_error_subclasses(self) -> None:
        subclasses = [
            AuthenticationError,
            ForbiddenError,
            NotFoundError,
            PaymentRequiredError,
            RateLimitError,
            ValidationError,
            ServerError,
        ]
        for cls in subclasses:
            assert issubclass(cls, MoltbotDenError), f"{cls.__name__} not subclass"


# ---------------------------------------------------------------------------
# raise_for_status -- success cases
# ---------------------------------------------------------------------------

class TestRaiseForStatusSuccess:
    """Verify that 2xx responses pass through without raising."""

    @pytest.mark.parametrize("status", [200, 201, 202, 204, 299])
    def test_2xx_does_not_raise(self, status: int) -> None:
        resp = _make_response(status, json_body={"ok": True})
        raise_for_status(resp)  # should not raise


# ---------------------------------------------------------------------------
# raise_for_status -- error mapping
# ---------------------------------------------------------------------------

class TestRaiseForStatusErrors:
    """Verify that each error status code maps to the correct exception."""

    def test_401_raises_authentication_error(self) -> None:
        resp = _make_response(401, json_body={"detail": "Invalid API key"})
        with pytest.raises(AuthenticationError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 401
        assert "Invalid API key" in exc_info.value.message

    def test_402_raises_payment_required_error(self) -> None:
        resp = _make_response(402, json_body={"detail": "Insufficient credits"})
        with pytest.raises(PaymentRequiredError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 402
        assert "Insufficient credits" in exc_info.value.message

    def test_403_raises_forbidden_error(self) -> None:
        resp = _make_response(403, json_body={"detail": "Provisional agents cannot do this"})
        with pytest.raises(ForbiddenError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 403

    def test_404_raises_not_found_error(self) -> None:
        resp = _make_response(404, json_body={"detail": "Agent not found"})
        with pytest.raises(NotFoundError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 404
        assert "Agent not found" in exc_info.value.message

    def test_422_raises_validation_error(self) -> None:
        resp = _make_response(422, json_body={"detail": "field required"})
        with pytest.raises(ValidationError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 422

    def test_400_raises_validation_error(self) -> None:
        resp = _make_response(400, json_body={"detail": "Bad request body"})
        with pytest.raises(ValidationError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 400
        assert "Bad request body" in exc_info.value.message

    def test_429_raises_rate_limit_error_without_retry_after(self) -> None:
        resp = _make_response(429, json_body={"detail": "Too many requests"})
        with pytest.raises(RateLimitError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after is None

    def test_429_raises_rate_limit_error_with_retry_after(self) -> None:
        resp = _make_response(
            429,
            json_body={"detail": "Slow down"},
            headers={"Retry-After": "60"},
        )
        with pytest.raises(RateLimitError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.retry_after == 60.0

    def test_429_with_invalid_retry_after_header(self) -> None:
        resp = _make_response(
            429,
            json_body={"detail": "Rate limited"},
            headers={"Retry-After": "not-a-number"},
        )
        with pytest.raises(RateLimitError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.retry_after is None

    def test_500_raises_server_error(self) -> None:
        resp = _make_response(500, json_body={"detail": "Internal error"})
        with pytest.raises(ServerError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 500

    def test_502_raises_server_error(self) -> None:
        resp = _make_response(502, json_body={"detail": "Bad gateway"})
        with pytest.raises(ServerError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 502

    def test_503_raises_server_error(self) -> None:
        resp = _make_response(503, text_body="Service Unavailable")
        with pytest.raises(ServerError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 503

    def test_unknown_4xx_raises_generic_moltbotden_error(self) -> None:
        resp = _make_response(418, json_body={"detail": "I'm a teapot"})
        with pytest.raises(MoltbotDenError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.status_code == 418
        # Should NOT be a more specific subclass
        assert type(exc_info.value) is MoltbotDenError


# ---------------------------------------------------------------------------
# raise_for_status -- body parsing
# ---------------------------------------------------------------------------

class TestRaiseForStatusBodyParsing:
    """Verify error body parsing with JSON and non-JSON responses."""

    def test_json_body_with_detail_key(self) -> None:
        resp = _make_response(404, json_body={"detail": "Not here"})
        with pytest.raises(NotFoundError) as exc_info:
            raise_for_status(resp)
        assert exc_info.value.message == "Not here"
        assert exc_info.value.body == {"detail": "Not here"}

    def test_json_body_without_detail_key(self) -> None:
        resp = _make_response(404, json_body={"error": "gone", "code": 404})
        with pytest.raises(NotFoundError) as exc_info:
            raise_for_status(resp)
        # Falls back to str(body) since no 'detail' key
        assert exc_info.value.body == {"error": "gone", "code": 404}

    def test_plain_text_body(self) -> None:
        resp = _make_response(500, text_body="Internal Server Error")
        with pytest.raises(ServerError) as exc_info:
            raise_for_status(resp)
        assert "Internal Server Error" in exc_info.value.message

    def test_empty_body(self) -> None:
        resp = _make_response(401)
        with pytest.raises(AuthenticationError):
            raise_for_status(resp)
