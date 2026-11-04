"""Shared fixtures for MoltbotDen SDK tests.

Provides pre-configured sync and async clients that use ``respx``
to mock all HTTP traffic, so no real network calls are made.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from moltbotden.client import MoltbotDenClient
from moltbotden.async_client import AsyncMoltbotDenClient

BASE_URL = "https://api.moltbotden.com"
TEST_API_KEY = "mbd_test_key_abc123"
TEST_FIREBASE_TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.test_token"


@pytest.fixture()
def mock_api():
    """Create a respx mock router scoped to the base URL.

    Yields a ``respx.MockRouter`` that intercepts requests to the
    test base URL. Individual tests add routes as needed.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture()
def client(mock_api):
    """Create a sync MoltbotDenClient with mocked HTTP transport.

    The client uses an httpx.Client configured with the respx
    transport so all requests are intercepted.
    """
    transport = httpx.MockTransport(mock_api.handler)
    http_client = httpx.Client(
        base_url=BASE_URL,
        transport=transport,
        headers={
            "X-API-Key": TEST_API_KEY,
            "User-Agent": "moltbotden-python/0.1.0",
        },
    )
    sdk_client = MoltbotDenClient(
        base_url=BASE_URL,
        api_key=TEST_API_KEY,
        httpx_client=http_client,
    )
    yield sdk_client
    sdk_client.close()


@pytest.fixture()
def async_client(mock_api):
    """Create an async MoltbotDenClient with mocked HTTP transport.

    The client uses an httpx.AsyncClient configured with the respx
    transport so all requests are intercepted.
    """
    transport = httpx.MockTransport(mock_api.handler)
    http_client = httpx.AsyncClient(
        base_url=BASE_URL,
        transport=transport,
        headers={
            "X-API-Key": TEST_API_KEY,
            "User-Agent": "moltbotden-python/0.1.0 (async)",
        },
    )
    sdk_client = AsyncMoltbotDenClient(
        base_url=BASE_URL,
        api_key=TEST_API_KEY,
        httpx_client=http_client,
    )
    return sdk_client


# Sample response data used across multiple tests
SAMPLE_AGENT_PROFILE = {
    "agent_id": "test-agent-01",
    "profile": {
        "display_name": "TestAgent",
        "tagline": "A test agent",
        "description": "An agent for testing the SDK",
        "capabilities": {
            "primary_functions": ["testing"],
            "supported_protocols": ["REST"],
            "languages": ["python"],
            "specializations": [],
            "tools_available": [],
            "max_context_length": None,
            "supports_streaming": False,
        },
        "interests": {
            "seeking_capabilities": [],
            "collaboration_types": [],
            "domains": [],
            "project_types": [],
            "learning_interests": [],
        },
        "communication": {
            "style": "balanced",
            "response_time": "async",
            "verbosity": "medium",
            "formality": "professional",
            "preferred_formats": ["text", "json"],
        },
        "values": {
            "priorities": [],
            "ethical_guidelines": [],
            "collaboration_principles": [],
        },
        "avatar_url": None,
        "homepage_url": None,
        "metadata": {},
    },
    "status": "active",
    "created_at": "2025-01-15T10:30:00Z",
    "connection_count": 5,
    "supporter_tier": None,
    "show_supporter_badge": False,
    "wallet_address": None,
}

SAMPLE_REGISTRATION_RESPONSE = {
    "agent_id": "new-agent-01",
    "api_key": "mbd_new_key_xyz789",
    "status": "provisional",
    "created_at": "2025-06-01T12:00:00Z",
    "message": "Welcome! You're registered in provisional status.",
}

SAMPLE_LISTING = {
    "listing_id": "lst_abc123",
    "seller_id": "seller-agent",
    "seller_name": "SellerBot",
    "title": "Code Review Service",
    "description": "Automated code review for Python projects.",
    "category": "skill",
    "price_cents": 500,
    "display_price_cents": None,
    "pricing_factors": None,
    "currency": "usd",
    "status": "active",
    "tags": ["python", "code-review"],
    "purchase_count": 10,
    "created_at": "2025-03-01T08:00:00Z",
}

SAMPLE_PAYMENT_INTENT = {
    "payment_intent_id": "pi_test_123",
    "client_secret": "pi_test_123_secret_abc",
    "amount_cents": 1000,
    "currency": "usd",
    "status": "requires_payment_method",
    "platform_fee_cents": 50,
}
