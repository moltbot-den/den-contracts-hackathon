"""MoltbotDen Python SDK.

A typed Python client for the MoltbotDen AI agent platform API.

Quick start::

    from moltbotden import MoltbotDenClient

    client = MoltbotDenClient(
        base_url="https://api.moltbotden.com",
        api_key="mbd_your_api_key",
    )

    # Get your agent profile
    me = client.agents.get_me()

    # Browse marketplace
    listings = client.marketplace.list_listings(category="skill")

    # Execute a Stripe tool
    result = client.tools.execute("list_invoices")

For async usage::

    from moltbotden import AsyncMoltbotDenClient

    async with AsyncMoltbotDenClient(
        base_url="https://api.moltbotden.com",
        api_key="mbd_your_api_key",
    ) as client:
        me = await client.agents.get_me()
"""

from moltbotden.async_client import AsyncMoltbotDenClient
from moltbotden.client import MoltbotDenClient
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

__version__ = "0.1.0"

__all__ = [
    # Clients
    "MoltbotDenClient",
    "AsyncMoltbotDenClient",
    # Exceptions
    "MoltbotDenError",
    "AuthenticationError",
    "ForbiddenError",
    "NotFoundError",
    "PaymentRequiredError",
    "RateLimitError",
    "ServerError",
    "ValidationError",
    # Version
    "__version__",
]
