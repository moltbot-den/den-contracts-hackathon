"""Marketplace-related Pydantic models for the SDK.

Covers marketplace listings, purchases, credits, and revenue.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ListingCategory(str, Enum):
    """Marketplace listing categories."""

    SKILL = "skill"
    DATA = "data"
    MODEL = "model"
    TEMPLATE = "template"
    SERVICE = "service"
    OTHER = "other"


class ListingStatus(str, Enum):
    """Marketplace listing statuses."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    SOLD_OUT = "sold_out"
    REMOVED = "removed"


class PaymentMethod(str, Enum):
    """Supported payment methods for marketplace purchases."""

    STRIPE = "stripe"
    X402 = "x402"
    CREDITS = "credits"


class CreateListingRequest(BaseModel):
    """Request payload to create a marketplace listing."""

    title: str
    description: str
    category: ListingCategory = ListingCategory.SKILL
    price_cents: int = Field(..., ge=50, le=10000000)
    currency: str = "usd"
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, str] = Field(default_factory=dict)


class MarketplaceListing(BaseModel):
    """Public view of a marketplace listing."""

    listing_id: str
    seller_id: str
    seller_name: str
    title: str
    description: str
    category: str
    price_cents: int
    display_price_cents: Optional[int] = None
    pricing_factors: Optional[Dict[str, Any]] = None
    currency: str = "usd"
    status: str = "active"
    tags: List[str] = Field(default_factory=list)
    purchase_count: int = 0
    created_at: Optional[datetime] = None


class PurchaseRequest(BaseModel):
    """Request payload to purchase a marketplace listing."""

    payment_method: PaymentMethod = PaymentMethod.STRIPE
    payment_proof: Optional[str] = None


class PurchaseResponse(BaseModel):
    """Response after initiating a marketplace purchase."""

    message: str
    purchase_id: str
    payment_method: str
    stripe_payment_intent_id: Optional[str] = None
    amount_cents: int
    base_price_cents: int
    platform_fee_cents: int = 0
    seller_revenue_cents: int = 0
    dynamic_pricing_applied: bool = False


class RevenueResponse(BaseModel):
    """Revenue summary for the authenticated seller."""

    agent_id: str
    total_revenue_cents: int = 0
    total_sales: int = 0
    listings_count: int = 0
    listings: List[Dict[str, Any]] = Field(default_factory=list)


class CreditsBalance(BaseModel):
    """Agent credits balance."""

    agent_id: str
    credits_balance: int = 0
