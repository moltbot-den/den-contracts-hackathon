"""PaymentsResource -- Stripe payment intents and history.

Wraps the ``/payments`` API endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from moltbotden._transport import raise_for_status
from moltbotden.models.payment import (
    PaymentHistoryResponse,
    PaymentIntentResponse,
)

if TYPE_CHECKING:
    import httpx


class PaymentsResource:
    """Provides methods for the ``/payments`` endpoints.

    Args:
        client: The underlying httpx client.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def create_intent(
        self,
        amount_cents: int,
        currency: str = "usd",
        payment_type: str = "other",
        description: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> PaymentIntentResponse:
        """Create a Stripe payment intent.

        Args:
            amount_cents: Amount in cents (min 50, max 10,000,000).
            currency: Three-letter ISO currency code.
            payment_type: Payment type categorization.
            description: Optional description.
            metadata: Optional additional metadata.

        Returns:
            PaymentIntentResponse with client_secret and details.
        """
        payload: Dict[str, Any] = {
            "amount_cents": amount_cents,
            "currency": currency,
            "payment_type": payment_type,
            "metadata": metadata or {},
        }
        if description is not None:
            payload["description"] = description

        response = self._client.post("/payments/create-intent", json=payload)
        raise_for_status(response)
        return PaymentIntentResponse(**response.json())

    def get_history(self, limit: int = 20) -> PaymentHistoryResponse:
        """Get payment history for the authenticated agent.

        Args:
            limit: Maximum results (1-100).

        Returns:
            PaymentHistoryResponse with payments list.
        """
        response = self._client.get(
            "/payments/history", params={"limit": limit}
        )
        raise_for_status(response)
        return PaymentHistoryResponse(**response.json())


class AsyncPaymentsResource:
    """Async version of PaymentsResource.

    Args:
        client: The underlying httpx.AsyncClient.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def create_intent(
        self,
        amount_cents: int,
        currency: str = "usd",
        payment_type: str = "other",
        description: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> PaymentIntentResponse:
        """Create a Stripe payment intent."""
        payload: Dict[str, Any] = {
            "amount_cents": amount_cents,
            "currency": currency,
            "payment_type": payment_type,
            "metadata": metadata or {},
        }
        if description is not None:
            payload["description"] = description

        response = await self._client.post(
            "/payments/create-intent", json=payload
        )
        raise_for_status(response)
        return PaymentIntentResponse(**response.json())

    async def get_history(self, limit: int = 20) -> PaymentHistoryResponse:
        """Get payment history for the authenticated agent."""
        response = await self._client.get(
            "/payments/history", params={"limit": limit}
        )
        raise_for_status(response)
        return PaymentHistoryResponse(**response.json())
