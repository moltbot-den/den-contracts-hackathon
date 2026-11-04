"""Wallet-related Pydantic models for the SDK.

Covers CDP wallet operations (send, trade, stake) as well as
on-chain data queries (balances, transactions, P&L, portfolio).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WalletInfo(BaseModel):
    """Agent's CDP wallet information."""

    has_wallet: bool = False
    wallet_id: Optional[str] = None
    wallet_address: Optional[str] = None
    network: Optional[str] = None
    cdp_wallets: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class WalletBalance(BaseModel):
    """Wallet balance response."""

    balances: List[Dict[str, Any]] = Field(default_factory=list)
    total_usd: Optional[float] = None


class SendPaymentRequest(BaseModel):
    """Request to send crypto from an agent's wallet."""

    to_address: str
    amount: str
    asset: str = "usdc"


class SendPaymentResponse(BaseModel):
    """Response after sending crypto."""

    tx_hash: str
    status: str
    amount: str
    asset: str
    to: str


class TradeTokensRequest(BaseModel):
    """Request to trade (swap) tokens."""

    from_asset: str
    to_asset: str
    amount: str


class TradeTokensResponse(BaseModel):
    """Response after a token trade."""

    trade_id: str
    from_asset: str
    to_asset: str
    amount: str
    status: str


class StakeTokensRequest(BaseModel):
    """Request to stake tokens for yield."""

    amount: str
    asset: str = "eth"


class StakeTokensResponse(BaseModel):
    """Response after staking tokens."""

    stake_id: str
    amount: str
    asset: str
    status: str
    validator_address: Optional[str] = None


class OnchainBalancesResponse(BaseModel):
    """On-chain wallet balances from Allium."""

    address: Optional[str] = None
    chain: Optional[str] = None
    balances: List[Dict[str, Any]] = Field(default_factory=list)
    powered_by: str = "Allium"


class OnchainTransactionsResponse(BaseModel):
    """On-chain transaction history from Allium."""

    data: List[Dict[str, Any]] = Field(default_factory=list)
    transactions: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    powered_by: str = "Allium"


class OnchainPnlResponse(BaseModel):
    """On-chain P&L analysis from Allium."""

    address: str
    chain: str
    total_pnl_usd: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    powered_by: str = "Allium"


class PortfolioResponse(BaseModel):
    """Combined portfolio view with balances and USD values."""

    address: str
    chain: str
    holdings: List[Dict[str, Any]] = Field(default_factory=list)
    total_usd: float = 0.0
    powered_by: str = "Allium"


class ReputationResponse(BaseModel):
    """On-chain reputation score response."""

    address: Optional[str] = None
    chain: Optional[str] = None
    score: Optional[int] = None
    breakdown: Optional[Dict[str, Any]] = None


class BadgesResponse(BaseModel):
    """DeFi activity badges response."""

    address: Optional[str] = None
    chain: Optional[str] = None
    badges: List[Dict[str, Any]] = Field(default_factory=list)


class PerksResponse(BaseModel):
    """MDEN holder perks and tier response."""

    address: Optional[str] = None
    chain: Optional[str] = None
    tier: Optional[str] = None
    perks: List[Dict[str, Any]] = Field(default_factory=list)
