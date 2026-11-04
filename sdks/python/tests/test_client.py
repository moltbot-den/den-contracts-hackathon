"""Tests for moltbotden.client and moltbotden.async_client.

Covers:
- MoltbotDenClient initialization with api_key and firebase_token
- Resource attribute presence (agents, wallet, marketplace, payments, graph, tools)
- base_url property
- Context manager protocol
- Trailing-slash normalization
- External httpx_client injection
- AsyncMoltbotDenClient mirrors the same behaviour
"""

from __future__ import annotations

import httpx
import pytest

from moltbotden.async_client import AsyncMoltbotDenClient
from moltbotden.client import MoltbotDenClient
from moltbotden.resources.agents import AgentsResource, AsyncAgentsResource
from moltbotden.resources.graph import GraphResource, AsyncGraphResource
from moltbotden.resources.marketplace import MarketplaceResource, AsyncMarketplaceResource
from moltbotden.resources.payments import PaymentsResource, AsyncPaymentsResource
from moltbotden.resources.tools import ToolsResource, AsyncToolsResource
from moltbotden.resources.wallet import WalletResource, AsyncWalletResource


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------

class TestMoltbotDenClient:
    """Tests for the synchronous MoltbotDenClient."""

    def test_init_with_api_key(self) -> None:
        client = MoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
        )
        try:
            assert client.base_url == "https://api.example.com"
        finally:
            client.close()

    def test_init_with_firebase_token(self) -> None:
        client = MoltbotDenClient(
            base_url="https://api.example.com",
            firebase_token="eyJ_tok",
        )
        try:
            assert client.base_url == "https://api.example.com"
        finally:
            client.close()

    def test_raises_when_no_auth_provided(self) -> None:
        with pytest.raises(ValueError, match="must be provided"):
            MoltbotDenClient(base_url="https://api.example.com")

    def test_raises_when_both_auth_provided(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            MoltbotDenClient(
                base_url="https://api.example.com",
                api_key="key",
                firebase_token="token",
            )

    def test_strips_trailing_slash_from_base_url(self) -> None:
        client = MoltbotDenClient(
            base_url="https://api.example.com/",
            api_key="mbd_test",
        )
        try:
            assert not client.base_url.endswith("/")
        finally:
            client.close()

    def test_resource_accessors_are_correct_types(self) -> None:
        client = MoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
        )
        try:
            assert isinstance(client.agents, AgentsResource)
            assert isinstance(client.wallet, WalletResource)
            assert isinstance(client.marketplace, MarketplaceResource)
            assert isinstance(client.payments, PaymentsResource)
            assert isinstance(client.graph, GraphResource)
            assert isinstance(client.tools, ToolsResource)
        finally:
            client.close()

    def test_context_manager(self) -> None:
        with MoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
        ) as client:
            assert isinstance(client, MoltbotDenClient)
            assert isinstance(client.agents, AgentsResource)

    def test_close_does_not_close_injected_client(self) -> None:
        http_client = httpx.Client(
            base_url="https://api.example.com",
            headers={"X-API-Key": "mbd_test"},
        )
        sdk = MoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
            httpx_client=http_client,
        )
        sdk.close()
        # Injected client should NOT be closed by sdk.close()
        assert not http_client.is_closed
        http_client.close()

    def test_close_closes_owned_client(self) -> None:
        client = MoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
        )
        client.close()
        assert client._http.is_closed

    def test_custom_timeout(self) -> None:
        client = MoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
            timeout=60.0,
        )
        try:
            # httpx stores timeout as a Timeout object
            assert client._http.timeout.connect == 60.0
        finally:
            client.close()

    def test_user_agent_header_set(self) -> None:
        client = MoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
        )
        try:
            ua = client._http.headers.get("user-agent")
            assert ua is not None
            assert "moltbotden-python" in ua
        finally:
            client.close()

    def test_api_key_header_set(self) -> None:
        client = MoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_my_key",
        )
        try:
            assert client._http.headers.get("x-api-key") == "mbd_my_key"
        finally:
            client.close()


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------

class TestAsyncMoltbotDenClient:
    """Tests for the asynchronous AsyncMoltbotDenClient."""

    def test_init_with_api_key(self) -> None:
        client = AsyncMoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
        )
        assert client.base_url == "https://api.example.com"

    def test_init_with_firebase_token(self) -> None:
        client = AsyncMoltbotDenClient(
            base_url="https://api.example.com",
            firebase_token="eyJ_tok",
        )
        assert client.base_url == "https://api.example.com"

    def test_raises_when_no_auth_provided(self) -> None:
        with pytest.raises(ValueError, match="must be provided"):
            AsyncMoltbotDenClient(base_url="https://api.example.com")

    def test_raises_when_both_auth_provided(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            AsyncMoltbotDenClient(
                base_url="https://api.example.com",
                api_key="key",
                firebase_token="token",
            )

    def test_strips_trailing_slash(self) -> None:
        client = AsyncMoltbotDenClient(
            base_url="https://api.example.com///",
            api_key="mbd_test",
        )
        assert not client.base_url.endswith("/")

    def test_resource_accessors_are_async_types(self) -> None:
        client = AsyncMoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
        )
        assert isinstance(client.agents, AsyncAgentsResource)
        assert isinstance(client.wallet, AsyncWalletResource)
        assert isinstance(client.marketplace, AsyncMarketplaceResource)
        assert isinstance(client.payments, AsyncPaymentsResource)
        assert isinstance(client.graph, AsyncGraphResource)
        assert isinstance(client.tools, AsyncToolsResource)

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        async with AsyncMoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
        ) as client:
            assert isinstance(client, AsyncMoltbotDenClient)
            assert isinstance(client.agents, AsyncAgentsResource)

    @pytest.mark.asyncio
    async def test_close_does_not_close_injected_client(self) -> None:
        http_client = httpx.AsyncClient(
            base_url="https://api.example.com",
            headers={"X-API-Key": "mbd_test"},
        )
        sdk = AsyncMoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
            httpx_client=http_client,
        )
        await sdk.close()
        assert not http_client.is_closed
        await http_client.aclose()

    def test_user_agent_header_contains_async(self) -> None:
        client = AsyncMoltbotDenClient(
            base_url="https://api.example.com",
            api_key="mbd_test",
        )
        ua = client._http.headers.get("user-agent")
        assert ua is not None
        assert "async" in ua
