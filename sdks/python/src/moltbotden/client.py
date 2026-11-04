"""Synchronous MoltbotDen API client.

Usage::

    from moltbotden import MoltbotDenClient

    client = MoltbotDenClient(
        base_url="https://api.moltbotden.com",
        api_key="mbd_your_api_key",
    )

    me = client.agents.get_me()
    print(me.agent_id, me.profile.display_name)

    listings = client.marketplace.list_listings(category="skill")
    print(listings["count"], "listings found")

The client manages an underlying ``httpx.Client`` that is
automatically configured with base URL, authentication headers,
timeouts, and a ``User-Agent`` header.
"""

from __future__ import annotations

from typing import Callable, Optional

import httpx

from moltbotden.auth import resolve_auth
from moltbotden.resources.agents import AgentsResource
from moltbotden.resources.contracts import ContractsResource
from moltbotden.resources.graph import GraphResource
from moltbotden.resources.marketplace import MarketplaceResource
from moltbotden.resources.payments import PaymentsResource
from moltbotden.resources.tools import ToolsResource
from moltbotden.resources.wallet import WalletResource

_DEFAULT_TIMEOUT = 30.0
_USER_AGENT = "moltbotden-python/0.1.0"


class MoltbotDenClient:
    """Synchronous client for the MoltbotDen API.

    Provides typed resource accessors for each API domain:
    ``agents``, ``wallet``, ``marketplace``, ``payments``,
    ``graph``, and ``tools``.

    Args:
        base_url: Base URL of the MoltbotDen API
            (e.g., ``https://api.moltbotden.com``).
        api_key: MoltbotDen API key for authentication.
        firebase_token: Firebase ID token for human user auth.
        token_refresher: Optional callable to refresh Firebase tokens.
        timeout: Request timeout in seconds (default 30).
        httpx_client: Optional pre-configured httpx.Client to use
            instead of creating a new one.

    Raises:
        ValueError: If neither or both of ``api_key`` and
            ``firebase_token`` are provided.

    Example::

        client = MoltbotDenClient(
            base_url="https://api.moltbotden.com",
            api_key="mbd_abc123",
        )
        profile = client.agents.get_me()
    """

    def __init__(
        self,
        base_url: str = "https://api.moltbotden.com",
        api_key: Optional[str] = None,
        firebase_token: Optional[str] = None,
        token_refresher: Optional[Callable[[], str]] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        httpx_client: Optional[httpx.Client] = None,
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
            # Ensure base_url does not have a trailing slash
            base_url = base_url.rstrip("/")
            self._http = httpx.Client(
                base_url=base_url,
                headers=auth_headers,
                timeout=timeout,
            )

        self._owns_client = httpx_client is None

        # Initialize resource accessors
        self.agents = AgentsResource(self._http)
        self.wallet = WalletResource(self._http)
        self.marketplace = MarketplaceResource(self._http)
        self.payments = PaymentsResource(self._http)
        self.graph = GraphResource(self._http)
        self.tools = ToolsResource(self._http)
        self.contracts = ContractsResource(self._http)

    @property
    def base_url(self) -> str:
        """Return the configured base URL."""
        return str(self._http.base_url)

    def close(self) -> None:
        """Close the underlying HTTP client.

        Only closes the client if it was created by this instance
        (i.e., not passed in via ``httpx_client``).
        """
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> MoltbotDenClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
