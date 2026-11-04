"""Tests for moltbotden.auth -- API key and Firebase token authentication.

Covers:
- ApiKeyAuth header generation and validation
- FirebaseTokenAuth header generation, refresh, and manual token update
- resolve_auth factory function for choosing the correct provider
- Edge cases: empty strings, whitespace-only, both credentials, neither
"""

from __future__ import annotations

import pytest

from moltbotden.auth import (
    ApiKeyAuth,
    AuthProvider,
    FirebaseTokenAuth,
    resolve_auth,
)


# ---------------------------------------------------------------------------
# ApiKeyAuth
# ---------------------------------------------------------------------------

class TestApiKeyAuth:
    """Tests for API key authentication provider."""

    def test_get_headers_returns_api_key_header(self) -> None:
        auth = ApiKeyAuth("mbd_test_key")
        headers = auth.get_headers()
        assert headers == {"X-API-Key": "mbd_test_key"}

    def test_strips_whitespace_from_key(self) -> None:
        auth = ApiKeyAuth("  mbd_test_key  ")
        assert auth.get_headers() == {"X-API-Key": "mbd_test_key"}

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ApiKeyAuth("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ApiKeyAuth("   ")

    def test_is_auth_provider_subclass(self) -> None:
        auth = ApiKeyAuth("mbd_key")
        assert isinstance(auth, AuthProvider)


# ---------------------------------------------------------------------------
# FirebaseTokenAuth
# ---------------------------------------------------------------------------

class TestFirebaseTokenAuth:
    """Tests for Firebase ID token authentication provider."""

    def test_get_headers_returns_bearer_token(self) -> None:
        auth = FirebaseTokenAuth("eyJ_test_token")
        headers = auth.get_headers()
        assert headers == {"Authorization": "Bearer eyJ_test_token"}

    def test_strips_whitespace_from_token(self) -> None:
        auth = FirebaseTokenAuth("  eyJ_token  ")
        assert auth.get_headers() == {"Authorization": "Bearer eyJ_token"}

    def test_rejects_empty_token(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            FirebaseTokenAuth("")

    def test_rejects_whitespace_only_token(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            FirebaseTokenAuth("   ")

    def test_token_property_getter(self) -> None:
        auth = FirebaseTokenAuth("abc123")
        assert auth.token == "abc123"

    def test_token_property_setter(self) -> None:
        auth = FirebaseTokenAuth("old_token")
        auth.token = "new_token"
        assert auth.token == "new_token"
        assert auth.get_headers() == {"Authorization": "Bearer new_token"}

    def test_token_setter_strips_whitespace(self) -> None:
        auth = FirebaseTokenAuth("old")
        auth.token = "  refreshed  "
        assert auth.token == "refreshed"

    def test_refresh_calls_refresher(self) -> None:
        call_count = 0

        def refresher() -> str:
            nonlocal call_count
            call_count += 1
            return f"fresh_token_{call_count}"

        auth = FirebaseTokenAuth("initial", token_refresher=refresher)
        new = auth.refresh()

        assert new == "fresh_token_1"
        assert auth.token == "fresh_token_1"
        assert auth.get_headers() == {"Authorization": "Bearer fresh_token_1"}

    def test_refresh_without_refresher_raises(self) -> None:
        auth = FirebaseTokenAuth("token_no_refresh")
        with pytest.raises(RuntimeError, match="No token_refresher configured"):
            auth.refresh()

    def test_is_auth_provider_subclass(self) -> None:
        auth = FirebaseTokenAuth("tok")
        assert isinstance(auth, AuthProvider)


# ---------------------------------------------------------------------------
# resolve_auth
# ---------------------------------------------------------------------------

class TestResolveAuth:
    """Tests for the resolve_auth factory function."""

    def test_returns_api_key_auth_for_api_key(self) -> None:
        result = resolve_auth(api_key="mbd_key")
        assert isinstance(result, ApiKeyAuth)
        assert result.get_headers() == {"X-API-Key": "mbd_key"}

    def test_returns_firebase_auth_for_token(self) -> None:
        result = resolve_auth(firebase_token="eyJ_tok")
        assert isinstance(result, FirebaseTokenAuth)
        assert result.get_headers() == {"Authorization": "Bearer eyJ_tok"}

    def test_firebase_with_refresher(self) -> None:
        refresher = lambda: "refreshed"  # noqa: E731
        result = resolve_auth(firebase_token="eyJ_tok", token_refresher=refresher)
        assert isinstance(result, FirebaseTokenAuth)
        assert result.refresh() == "refreshed"

    def test_raises_when_both_provided(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            resolve_auth(api_key="key", firebase_token="token")

    def test_raises_when_neither_provided(self) -> None:
        with pytest.raises(ValueError, match="must be provided"):
            resolve_auth()

    def test_raises_when_api_key_is_empty(self) -> None:
        with pytest.raises(ValueError, match="must be provided"):
            resolve_auth(api_key="")

    def test_raises_when_token_is_empty(self) -> None:
        with pytest.raises(ValueError, match="must be provided"):
            resolve_auth(firebase_token="")

    def test_raises_when_api_key_is_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="must be provided"):
            resolve_auth(api_key="   ")

    def test_raises_when_both_are_whitespace(self) -> None:
        """Whitespace-only values are treated as absent."""
        with pytest.raises(ValueError, match="must be provided"):
            resolve_auth(api_key="  ", firebase_token="  ")
