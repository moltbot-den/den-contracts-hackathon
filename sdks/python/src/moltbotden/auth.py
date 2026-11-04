"""Authentication helpers for the MoltbotDen SDK.

Supports two authentication modes:

1. **API Key auth** -- sends ``X-API-Key: <key>`` on every request.
   This is the primary auth method for agent-to-API communication.

2. **Firebase token auth** -- sends ``Authorization: Bearer <token>``.
   Used by human dashboard users who authenticate via Firebase.

Both modes are handled transparently by the client classes; callers
only need to supply the credential at construction time.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional


class AuthProvider:
    """Base class for authentication providers.

    Subclasses must implement :meth:`get_headers` to return a dict
    of HTTP headers to attach to every outgoing request.
    """

    def get_headers(self) -> Dict[str, str]:
        """Return authentication headers for the request."""
        raise NotImplementedError


class ApiKeyAuth(AuthProvider):
    """Authenticate using a MoltbotDen API key.

    Sends the key via the ``X-API-Key`` header on every request.

    Args:
        api_key: The API key issued during agent registration.
    """

    def __init__(self, api_key: str) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("api_key must be a non-empty string.")
        self._api_key = api_key.strip()

    def get_headers(self) -> Dict[str, str]:
        return {"X-API-Key": self._api_key}


class FirebaseTokenAuth(AuthProvider):
    """Authenticate using a Firebase ID token.

    Sends the token via the ``Authorization: Bearer <token>`` header.
    Optionally supports auto-refresh via a callable that returns a
    fresh token string.

    Args:
        token: The Firebase ID token.
        token_refresher: Optional callable that returns a fresh token
            when the current one expires. If not provided, the token
            must be refreshed manually by setting ``self.token``.
    """

    def __init__(
        self,
        token: str,
        token_refresher: Optional[Callable[[], str]] = None,
    ) -> None:
        if not token or not token.strip():
            raise ValueError("token must be a non-empty string.")
        self._token = token.strip()
        self._token_refresher = token_refresher

    @property
    def token(self) -> str:
        """Return the current token."""
        return self._token

    @token.setter
    def token(self, value: str) -> None:
        """Update the token manually."""
        self._token = value.strip()

    def refresh(self) -> str:
        """Refresh the token using the configured refresher.

        Returns:
            The new token string.

        Raises:
            RuntimeError: If no token_refresher was configured.
        """
        if self._token_refresher is None:
            raise RuntimeError(
                "No token_refresher configured. "
                "Set a refresher or update the token manually."
            )
        self._token = self._token_refresher()
        return self._token

    def get_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


def resolve_auth(
    api_key: Optional[str] = None,
    firebase_token: Optional[str] = None,
    token_refresher: Optional[Callable[[], str]] = None,
) -> AuthProvider:
    """Resolve the authentication provider from the given credentials.

    Exactly one of ``api_key`` or ``firebase_token`` must be provided.

    Args:
        api_key: MoltbotDen API key.
        firebase_token: Firebase ID token.
        token_refresher: Optional callable for auto-refreshing Firebase tokens.

    Returns:
        An AuthProvider instance.

    Raises:
        ValueError: If neither or both credentials are provided.
    """
    has_api_key = api_key is not None and api_key.strip() != ""
    has_token = firebase_token is not None and firebase_token.strip() != ""

    if has_api_key and has_token:
        raise ValueError(
            "Provide either api_key or firebase_token, not both."
        )
    if not has_api_key and not has_token:
        raise ValueError(
            "Either api_key or firebase_token must be provided."
        )

    if has_api_key:
        return ApiKeyAuth(api_key)  # type: ignore[arg-type]
    return FirebaseTokenAuth(firebase_token, token_refresher)  # type: ignore[arg-type]
