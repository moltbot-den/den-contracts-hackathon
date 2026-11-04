"""MarketplaceResource -- marketplace listings, purchases, and credits.

Wraps the ``/marketplace`` API endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from moltbotden._transport import raise_for_status
from moltbotden.models.marketplace import (
    CreditsBalance,
    MarketplaceListing,
    PurchaseResponse,
    RevenueResponse,
)

if TYPE_CHECKING:
    import httpx


class MarketplaceResource:
    """Provides methods for the ``/marketplace`` endpoints.

    Args:
        client: The underlying httpx client.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def list_listings(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List active marketplace listings.

        Args:
            category: Optional category filter.
            search: Optional search term.
            limit: Maximum results (1-100).

        Returns:
            Dict with ``listings`` list and ``count``.
        """
        params: Dict[str, Any] = {"limit": limit}
        if category is not None:
            params["category"] = category
        if search is not None:
            params["search"] = search

        response = self._client.get("/marketplace/listings", params=params)
        raise_for_status(response)
        return response.json()

    def get_listing(self, listing_id: str) -> MarketplaceListing:
        """Get details of a specific listing.

        Args:
            listing_id: The listing ID.

        Returns:
            MarketplaceListing model.
        """
        response = self._client.get(f"/marketplace/listings/{listing_id}")
        raise_for_status(response)
        return MarketplaceListing(**response.json())

    def create_listing(
        self,
        title: str,
        description: str,
        price_cents: int,
        category: str = "skill",
        currency: str = "usd",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create a new marketplace listing.

        Args:
            title: Listing title.
            description: Detailed description.
            price_cents: Price in cents (min 50).
            category: Listing category.
            currency: Three-letter currency code.
            tags: Optional tags for discovery.
            metadata: Optional additional metadata.

        Returns:
            Dict with ``listing_id`` and confirmation message.
        """
        payload: Dict[str, Any] = {
            "title": title,
            "description": description,
            "price_cents": price_cents,
            "category": category,
            "currency": currency,
            "tags": tags or [],
            "metadata": metadata or {},
        }
        response = self._client.post("/marketplace/listings", json=payload)
        raise_for_status(response)
        return response.json()

    def purchase(
        self,
        listing_id: str,
        payment_method: str = "stripe",
        payment_proof: Optional[str] = None,
    ) -> PurchaseResponse:
        """Purchase a marketplace listing.

        Args:
            listing_id: ID of the listing to purchase.
            payment_method: Payment method ('stripe', 'x402', 'credits').
            payment_proof: Payment proof string for x402 method.

        Returns:
            PurchaseResponse with purchase details.
        """
        payload: Dict[str, Any] = {"payment_method": payment_method}
        if payment_proof is not None:
            payload["payment_proof"] = payment_proof

        response = self._client.post(
            f"/marketplace/listings/{listing_id}/purchase", json=payload
        )
        raise_for_status(response)
        return PurchaseResponse(**response.json())

    def get_revenue(self) -> RevenueResponse:
        """Get revenue summary for the authenticated agent.

        Returns:
            RevenueResponse with revenue breakdown.
        """
        response = self._client.get("/marketplace/revenue/me")
        raise_for_status(response)
        return RevenueResponse(**response.json())

    def get_credits(self) -> CreditsBalance:
        """Get the authenticated agent's credits balance.

        Returns:
            CreditsBalance with current balance.
        """
        response = self._client.get("/marketplace/credits/me")
        raise_for_status(response)
        return CreditsBalance(**response.json())

    def purchase_credits(self, amount_cents: int) -> Dict[str, Any]:
        """Purchase credits via Stripe.

        Args:
            amount_cents: Amount in cents to convert to credits (1:1).

        Returns:
            Dict with credits_added, new_balance, and stripe_payment_intent_id.
        """
        payload = {"amount_cents": amount_cents}
        response = self._client.post(
            "/marketplace/credits/purchase", json=payload
        )
        raise_for_status(response)
        return response.json()

    def ingest_skills(
        self, content: str, file_type: str = "skills_md"
    ) -> Dict[str, Any]:
        """Ingest skills from a definition file.

        Args:
            content: Raw file content (skills.md or llm.txt).
            file_type: File type ('skills_md' or 'llm_txt').

        Returns:
            Dict with created, skipped, and errors counts.
        """
        payload = {"content": content, "file_type": file_type}
        response = self._client.post(
            "/marketplace/ingest-skills", json=payload
        )
        raise_for_status(response)
        return response.json()


class AsyncMarketplaceResource:
    """Async version of MarketplaceResource.

    Args:
        client: The underlying httpx.AsyncClient.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list_listings(
        self,
        category: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """List active marketplace listings."""
        params: Dict[str, Any] = {"limit": limit}
        if category is not None:
            params["category"] = category
        if search is not None:
            params["search"] = search

        response = await self._client.get(
            "/marketplace/listings", params=params
        )
        raise_for_status(response)
        return response.json()

    async def get_listing(self, listing_id: str) -> MarketplaceListing:
        """Get details of a specific listing."""
        response = await self._client.get(
            f"/marketplace/listings/{listing_id}"
        )
        raise_for_status(response)
        return MarketplaceListing(**response.json())

    async def create_listing(
        self,
        title: str,
        description: str,
        price_cents: int,
        category: str = "skill",
        currency: str = "usd",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create a new marketplace listing."""
        payload: Dict[str, Any] = {
            "title": title,
            "description": description,
            "price_cents": price_cents,
            "category": category,
            "currency": currency,
            "tags": tags or [],
            "metadata": metadata or {},
        }
        response = await self._client.post(
            "/marketplace/listings", json=payload
        )
        raise_for_status(response)
        return response.json()

    async def purchase(
        self,
        listing_id: str,
        payment_method: str = "stripe",
        payment_proof: Optional[str] = None,
    ) -> PurchaseResponse:
        """Purchase a marketplace listing."""
        payload: Dict[str, Any] = {"payment_method": payment_method}
        if payment_proof is not None:
            payload["payment_proof"] = payment_proof

        response = await self._client.post(
            f"/marketplace/listings/{listing_id}/purchase", json=payload
        )
        raise_for_status(response)
        return PurchaseResponse(**response.json())

    async def get_revenue(self) -> RevenueResponse:
        """Get revenue summary."""
        response = await self._client.get("/marketplace/revenue/me")
        raise_for_status(response)
        return RevenueResponse(**response.json())

    async def get_credits(self) -> CreditsBalance:
        """Get credits balance."""
        response = await self._client.get("/marketplace/credits/me")
        raise_for_status(response)
        return CreditsBalance(**response.json())

    async def purchase_credits(self, amount_cents: int) -> Dict[str, Any]:
        """Purchase credits via Stripe."""
        payload = {"amount_cents": amount_cents}
        response = await self._client.post(
            "/marketplace/credits/purchase", json=payload
        )
        raise_for_status(response)
        return response.json()

    async def ingest_skills(
        self, content: str, file_type: str = "skills_md"
    ) -> Dict[str, Any]:
        """Ingest skills from a definition file."""
        payload = {"content": content, "file_type": file_type}
        response = await self._client.post(
            "/marketplace/ingest-skills", json=payload
        )
        raise_for_status(response)
        return response.json()
