"""Payment-related Pydantic models for the SDK.

Covers Stripe payment intents, payment history, and payment records.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PaymentStatus(str, Enum):
    """Payment processing status."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    REFUNDED = "refunded"


class PaymentType(str, Enum):
    """Type of payment transaction."""

    SKILL_PURCHASE = "skill_purchase"
    MEDIA_GENERATION = "media_generation"
    MARKETPLACE_LISTING = "marketplace_listing"
    DONATION = "donation"
    X402_PAYMENT = "x402_payment"
    OTHER = "other"


class CreatePaymentIntentRequest(BaseModel):
    """Request payload to create a Stripe payment intent."""

    amount_cents: int = Field(..., ge=50, le=10000000)
    currency: str = "usd"
    payment_type: PaymentType = PaymentType.OTHER
    description: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)


class PaymentIntentResponse(BaseModel):
    """Response after creating a payment intent."""

    payment_intent_id: str
    client_secret: str
    amount_cents: int
    currency: str
    status: str
    platform_fee_cents: int = 0


class PaymentRecord(BaseModel):
    """Payment record from history."""

    payment_id: Optional[str] = None
    agent_id: Optional[str] = None
    stripe_payment_intent_id: Optional[str] = None
    amount_cents: Optional[int] = None
    currency: str = "usd"
    status: Optional[str] = None
    payment_type: Optional[str] = None
    description: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)
    platform_fee_cents: int = 0
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class PaymentHistoryResponse(BaseModel):
    """Response for payment history queries."""

    payments: List[Any] = Field(default_factory=list)
    count: int = 0
    has_more: bool = False
