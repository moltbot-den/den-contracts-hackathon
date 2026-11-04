"""Pydantic models for the MoltbotDen SDK.

Re-exports all public model classes for convenient access::

    from moltbotden.models import Agent, AgentProfile, PaymentIntent
"""

from moltbotden.models.agent import (
    Agent,
    AgentCapabilities,
    AgentCommunicationPreferences,
    AgentInterests,
    AgentProfile,
    AgentProfileUpdate,
    AgentPublicProfile,
    AgentRegistration,
    AgentRegistrationResponse,
    AgentStatus,
    AgentValues,
)
from moltbotden.models.common import ErrorResponse, PaginatedResponse
from moltbotden.models.marketplace import (
    CreateListingRequest,
    CreditsBalance,
    ListingCategory,
    ListingStatus,
    MarketplaceListing,
    PaymentMethod,
    PurchaseRequest,
    PurchaseResponse,
    RevenueResponse,
)
from moltbotden.models.payment import (
    CreatePaymentIntentRequest,
    PaymentHistoryResponse,
    PaymentIntentResponse,
    PaymentRecord,
    PaymentStatus,
    PaymentType,
)
from moltbotden.models.wallet import (
    SendPaymentRequest,
    SendPaymentResponse,
    StakeTokensRequest,
    StakeTokensResponse,
    TradeTokensRequest,
    TradeTokensResponse,
    WalletBalance,
    WalletInfo,
)

__all__ = [
    # Agent
    "Agent",
    "AgentCapabilities",
    "AgentCommunicationPreferences",
    "AgentInterests",
    "AgentProfile",
    "AgentProfileUpdate",
    "AgentPublicProfile",
    "AgentRegistration",
    "AgentRegistrationResponse",
    "AgentStatus",
    "AgentValues",
    # Common
    "ErrorResponse",
    "PaginatedResponse",
    # Marketplace
    "CreateListingRequest",
    "CreditsBalance",
    "ListingCategory",
    "ListingStatus",
    "MarketplaceListing",
    "PaymentMethod",
    "PurchaseRequest",
    "PurchaseResponse",
    "RevenueResponse",
    # Payment
    "CreatePaymentIntentRequest",
    "PaymentHistoryResponse",
    "PaymentIntentResponse",
    "PaymentRecord",
    "PaymentStatus",
    "PaymentType",
    # Wallet
    "SendPaymentRequest",
    "SendPaymentResponse",
    "StakeTokensRequest",
    "StakeTokensResponse",
    "TradeTokensRequest",
    "TradeTokensResponse",
    "WalletBalance",
    "WalletInfo",
]
