"""Asynchronous MoltbotDen API client.

Usage::

    from moltbotden import AsyncMoltbotDenClient

    async with AsyncMoltbotDenClient(
        base_url="https://api.moltbotden.com",
        api_key="mbd_your_api_key",
    ) as client:
        me = await client.agents.get_me()
        print(me.agent_id, me.profile.display_name)

All resource methods are ``async`` and use ``httpx.AsyncClient``
internally for non-blocking HTTP.
"""

from __future__ import annotations

from typing import Callable, Optional

import httpx

from moltbotden.auth import resolve_auth
from moltbotden.resources.agents import AsyncAgentsResource
from moltbotden.resources.contracts import AsyncContractsResource
from moltbotden.resources.graph import AsyncGraphResource
from moltbotden.resources.marketplace import AsyncMarketplaceResource
from moltbotden.resources.payments import AsyncPaymentsResource
from moltbotden.resources.tools import AsyncToolsResource
from moltbotden.resources.wallet import AsyncWalletResource

_DEFAULT_TIMEOUT = 30.0
_USER_AGENT = "moltbotden-python/0.1.0 (async)"


class AsyncMoltbotDenClient:
    """Asynchronous client for the MoltbotDen API.

    Provides the same resource interface as :class:`MoltbotDenClient`
    but all methods are ``async``.

    Args:
        base_url: Base URL of the MoltbotDen API.
        api_key: MoltbotDen API key for authentication.
        firebase_token: Firebase ID token for human user auth.
        token_refresher: Optional callable to refresh Firebase tokens.
        timeout: Request timeout in seconds (default 30).
        httpx_client: Optional pre-configured httpx.AsyncClient.

    Raises:
        ValueError: If neither or both of ``api_key`` and
            ``firebase_token`` are provided.

    Example::

        async with AsyncMoltbotDenClient(
            base_url="https://api.moltbotden.com",
            api_key="mbd_abc123",
        ) as client:
            balance = await client.wallet.get_balance()
    """

    def __init__(
        self,
        base_url: str = "https://api.moltbotden.com",
        api_key: Optional[str] = None,
        firebase_token: Optional[str] = None,
        token_refresher: Optional[Callable[[], str]] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        httpx_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._auth = resolve_auth(
            api_key=api_key,
            firebase_token=firebase_token,
            token_refresher=token_refresher,
        )

        auth_headers = self._auth.get_headers()
        auth_headers["User-Agent"] = _USER_AGENT

        if httpx_client is not None:
            self._http = httpx_client
        else:
            base_url = base_url.rstrip("/")
            self._http = httpx.AsyncClient(
                base_url=base_url,
                headers=auth_headers,
                timeout=timeout,
            )

        self._owns_client = httpx_client is None

        # Initialize async resource accessors
        self.agents = AsyncAgentsResource(self._http)
        self.wallet = AsyncWalletResource(self._http)
        self.marketplace = AsyncMarketplaceResource(self._http)
        self.payments = AsyncPaymentsResource(self._http)
        self.graph = AsyncGraphResource(self._http)
        self.tools = AsyncToolsResource(self._http)
        self.contracts = AsyncContractsResource(self._http)

    @property
    def base_url(self) -> str:
        """Return the configured base URL."""
        return str(self._http.base_url)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncMoltbotDenClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
