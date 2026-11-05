"""Tests for all resource modules with mocked HTTP responses.

Each resource class (agents, wallet, marketplace, payments, graph, tools)
is tested through the sync MoltbotDenClient using the ``client`` and
``mock_api`` fixtures from conftest.py (which use respx to intercept
HTTP traffic).

Also includes selected async tests using the ``async_client`` fixture
to verify the async code path works identically.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from moltbotden.exceptions import (
    AuthenticationError,
    ForbiddenError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitError,
)
from moltbotden.models.agent import AgentPublicProfile, AgentRegistrationResponse
from moltbotden.models.marketplace import (
    CreditsBalance,
    MarketplaceListing,
    PurchaseResponse,
    RevenueResponse,
)
from moltbotden.models.payment import PaymentHistoryResponse, PaymentIntentResponse

from .conftest import (
    BASE_URL,
    SAMPLE_AGENT_PROFILE,
    SAMPLE_LISTING,
    SAMPLE_PAYMENT_INTENT,
    SAMPLE_REGISTRATION_RESPONSE,
    TEST_API_KEY,
)


# ===========================================================================
# AgentsResource
# ===========================================================================

class TestAgentsResource:
    """Tests for client.agents methods."""

    def test_register(self, client, mock_api) -> None:
        mock_api.post("/agents/register").mock(
            return_value=httpx.Response(200, json=SAMPLE_REGISTRATION_RESPONSE)
        )
        result = client.agents.register(
            agent_id="new-agent-01",
            profile={"display_name": "NewBot"},
        )
        assert isinstance(result, AgentRegistrationResponse)
        assert result.agent_id == "new-agent-01"
        assert result.api_key == "mbd_new_key_xyz789"
        assert result.status.value == "provisional"

    def test_register_with_invite_code(self, client, mock_api) -> None:
        mock_api.post("/agents/register").mock(
            return_value=httpx.Response(200, json=SAMPLE_REGISTRATION_RESPONSE)
        )
        result = client.agents.register(
            agent_id="new-agent-01",
            profile={"display_name": "NewBot"},
            invite_code="INVITE123",
        )
        assert isinstance(result, AgentRegistrationResponse)

    def test_get_me(self, client, mock_api) -> None:
        mock_api.get("/agents/me").mock(
            return_value=httpx.Response(200, json=SAMPLE_AGENT_PROFILE)
        )
        result = client.agents.get_me()
        assert isinstance(result, AgentPublicProfile)
        assert result.agent_id == "test-agent-01"
        assert result.profile.display_name == "TestAgent"
        assert result.connection_count == 5

    def test_update_me(self, client, mock_api) -> None:
        updated = {**SAMPLE_AGENT_PROFILE, "profile": {**SAMPLE_AGENT_PROFILE["profile"], "tagline": "Updated!"}}
        mock_api.patch("/agents/me").mock(
            return_value=httpx.Response(200, json=updated)
        )
        result = client.agents.update_me({"tagline": "Updated!"})
        assert isinstance(result, AgentPublicProfile)
        assert result.profile.tagline == "Updated!"

    def test_get_agent_by_id(self, client, mock_api) -> None:
        mock_api.get("/agents/test-agent-01").mock(
            return_value=httpx.Response(200, json=SAMPLE_AGENT_PROFILE)
        )
        result = client.agents.get("test-agent-01")
        assert isinstance(result, AgentPublicProfile)
        assert result.agent_id == "test-agent-01"

    def test_get_agent_not_found(self, client, mock_api) -> None:
        mock_api.get("/agents/nonexistent").mock(
            return_value=httpx.Response(404, json={"detail": "Agent not found"})
        )
        with pytest.raises(NotFoundError):
            client.agents.get("nonexistent")

    def test_claim(self, client, mock_api) -> None:
        mock_api.post("/agents/test-agent-01/claim").mock(
            return_value=httpx.Response(200, json={"message": "Claimed", "agent_id": "test-agent-01"})
        )
        result = client.agents.claim("test-agent-01")
        assert result["message"] == "Claimed"

    def test_get_human_agents(self, client, mock_api) -> None:
        mock_api.get("/agents/human/my-agents").mock(
            return_value=httpx.Response(200, json={"agents": [], "count": 0})
        )
        result = client.agents.get_human_agents()
        assert result["count"] == 0
        assert result["agents"] == []

    def test_get_me_unauthorized(self, client, mock_api) -> None:
        mock_api.get("/agents/me").mock(
            return_value=httpx.Response(401, json={"detail": "Invalid API key"})
        )
        with pytest.raises(AuthenticationError) as exc_info:
            client.agents.get_me()
        assert "Invalid API key" in exc_info.value.message

    def test_heartbeat_hits_root_path(self, client, mock_api) -> None:
        """Regression: heartbeat MUST hit ``POST /heartbeat`` not ``/agents/heartbeat``.

        Pinned because the TS SDK shipped with the wrong path historically.
        """
        body = {
            "status": "ok",
            "timestamp": "2026-04-26T12:00:00+00:00",
            "pending_connections": 1,
            "unread_messages": 3,
            "email": {
                "provisioned": True,
                "email_address": "agent@agents.moltbotden.com",
                "unread_count": 2,
                "action": "GET /email/inbox?unread_only=true to read new messages",
            },
            "notifications": {"connection_requests": []},
        }
        route = mock_api.post("/heartbeat").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.agents.heartbeat()
        assert route.called
        assert result["status"] == "ok"
        assert result["pending_connections"] == 1
        assert result["email"]["provisioned"] is True

    def test_heartbeat_unauthorized_raises(self, client, mock_api) -> None:
        mock_api.post("/heartbeat").mock(
            return_value=httpx.Response(401, json={"detail": "Invalid API key"})
        )
        with pytest.raises(AuthenticationError):
            client.agents.heartbeat()


# ===========================================================================
# WalletResource
# ===========================================================================

class TestWalletResource:
    """Tests for client.wallet methods."""

    def test_get_info(self, client, mock_api) -> None:
        body = {
            "has_wallet": True,
            "wallet_id": "w_123",
            "wallet_address": "0xabc",
            "network": "base-sepolia",
        }
        mock_api.get("/wallet/me").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.get_info()
        assert result["has_wallet"] is True
        assert result["wallet_address"] == "0xabc"

    def test_create_wallet(self, client, mock_api) -> None:
        body = {"wallet_id": "w_new", "wallet_address": "0xdef", "network": "base-sepolia"}
        mock_api.post("/wallet/me/create").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.create(network="base-sepolia")
        assert result["wallet_id"] == "w_new"

    def test_create_wallet_default_network(self, client, mock_api) -> None:
        body = {"wallet_id": "w_new", "wallet_address": "0xdef", "network": "base-mainnet"}
        mock_api.post("/wallet/me/create").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.create()
        assert result["network"] == "base-mainnet"

    def test_get_balance(self, client, mock_api) -> None:
        body = {"eth": "1.5", "usdc": "100.00"}
        mock_api.get("/wallet/me/balance").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.get_balance()
        assert result["eth"] == "1.5"

    def test_fund(self, client, mock_api) -> None:
        body = {"status": "funded", "amount": "0.1", "asset": "eth"}
        mock_api.post("/wallet/me/fund").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.fund()
        assert result["status"] == "funded"

    def test_send(self, client, mock_api) -> None:
        body = {"tx_hash": "0xhash", "status": "pending", "amount": "10", "asset": "usdc", "to": "0xrecipient"}
        mock_api.post("/wallet/me/send").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.send(to_address="0xrecipient", amount="10", asset="usdc")
        assert result["tx_hash"] == "0xhash"

    def test_trade(self, client, mock_api) -> None:
        body = {"trade_id": "t_1", "from_asset": "eth", "to_asset": "usdc", "amount": "0.5", "status": "completed"}
        mock_api.post("/wallet/me/trade").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.trade(from_asset="eth", to_asset="usdc", amount="0.5")
        assert result["trade_id"] == "t_1"

    def test_stake(self, client, mock_api) -> None:
        body = {"stake_id": "s_1", "amount": "1.0", "asset": "eth", "status": "staked"}
        mock_api.post("/wallet/me/stake").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.stake(amount="1.0")
        assert result["status"] == "staked"

    def test_list_networks(self, client, mock_api) -> None:
        body = {"default_network": "base-mainnet", "networks": ["base-mainnet", "base-sepolia"]}
        mock_api.get("/wallet/me/networks").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.list_networks()
        assert "base-mainnet" in result["networks"]

    def test_get_onchain_balances(self, client, mock_api) -> None:
        body = {"address": "0xabc", "chain": "base", "tokens": []}
        mock_api.get("/wallet/balances/0xabc").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.get_onchain_balances("0xabc")
        assert result["address"] == "0xabc"

    def test_get_transactions(self, client, mock_api) -> None:
        body = {"transactions": [{"tx_hash": "0x1"}], "count": 1}
        mock_api.get("/wallet/transactions/0xabc").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.get_transactions("0xabc", limit=5)
        assert len(result["transactions"]) == 1

    def test_get_pnl(self, client, mock_api) -> None:
        body = {"total_pnl_usd": 150.0, "realized_pnl": 100.0, "unrealized_pnl": 50.0}
        mock_api.get("/wallet/pnl/0xabc").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.get_pnl("0xabc")
        assert result["total_pnl_usd"] == 150.0

    def test_get_portfolio(self, client, mock_api) -> None:
        body = {"holdings": [], "total_usd": 500.0}
        mock_api.get("/wallet/portfolio/0xabc").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.get_portfolio("0xabc")
        assert result["total_usd"] == 500.0

    def test_get_reputation(self, client, mock_api) -> None:
        body = {"score": 250, "breakdown": {"age": 50, "activity": 100, "diversity": 100}}
        mock_api.get("/wallet/reputation/0xabc").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.get_reputation("0xabc")
        assert result["score"] == 250

    def test_get_badges(self, client, mock_api) -> None:
        body = {"badges": [{"name": "DEX Trader", "earned": True}]}
        mock_api.get("/wallet/badges/0xabc").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.get_badges("0xabc")
        assert result["badges"][0]["name"] == "DEX Trader"

    def test_get_perks(self, client, mock_api) -> None:
        body = {"tier": "gold", "perks": ["reduced_fees", "priority_support"]}
        mock_api.get("/wallet/perks/0xabc").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.wallet.get_perks("0xabc")
        assert result["tier"] == "gold"


# ===========================================================================
# MarketplaceResource
# ===========================================================================

class TestMarketplaceResource:
    """Tests for client.marketplace methods."""

    def test_list_listings(self, client, mock_api) -> None:
        body = {"listings": [SAMPLE_LISTING], "count": 1}
        mock_api.get("/marketplace/listings").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.marketplace.list_listings()
        assert result["count"] == 1
        assert result["listings"][0]["listing_id"] == "lst_abc123"

    def test_list_listings_with_filters(self, client, mock_api) -> None:
        body = {"listings": [], "count": 0}
        mock_api.get("/marketplace/listings").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.marketplace.list_listings(
            category="data", search="python", limit=5
        )
        assert result["count"] == 0

    def test_get_listing(self, client, mock_api) -> None:
        mock_api.get("/marketplace/listings/lst_abc123").mock(
            return_value=httpx.Response(200, json=SAMPLE_LISTING)
        )
        result = client.marketplace.get_listing("lst_abc123")
        assert isinstance(result, MarketplaceListing)
        assert result.listing_id == "lst_abc123"
        assert result.title == "Code Review Service"
        assert result.price_cents == 500

    def test_get_listing_not_found(self, client, mock_api) -> None:
        mock_api.get("/marketplace/listings/bad_id").mock(
            return_value=httpx.Response(404, json={"detail": "Listing not found"})
        )
        with pytest.raises(NotFoundError):
            client.marketplace.get_listing("bad_id")

    def test_create_listing(self, client, mock_api) -> None:
        body = {"listing_id": "lst_new", "message": "Listing created"}
        mock_api.post("/marketplace/listings").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.marketplace.create_listing(
            title="My Skill",
            description="A great skill",
            price_cents=1000,
            category="skill",
            tags=["python", "testing"],
        )
        assert result["listing_id"] == "lst_new"

    def test_purchase(self, client, mock_api) -> None:
        body = {
            "message": "Purchase initiated",
            "purchase_id": "pur_123",
            "payment_method": "stripe",
            "stripe_payment_intent_id": "pi_xyz",
            "amount_cents": 500,
            "base_price_cents": 500,
            "platform_fee_cents": 25,
            "seller_revenue_cents": 475,
            "dynamic_pricing_applied": False,
        }
        mock_api.post("/marketplace/listings/lst_abc123/purchase").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.marketplace.purchase("lst_abc123")
        assert isinstance(result, PurchaseResponse)
        assert result.purchase_id == "pur_123"
        assert result.platform_fee_cents == 25

    def test_purchase_with_x402(self, client, mock_api) -> None:
        body = {
            "message": "Purchased via x402",
            "purchase_id": "pur_402",
            "payment_method": "x402",
            "amount_cents": 500,
            "base_price_cents": 500,
            "platform_fee_cents": 0,
            "seller_revenue_cents": 500,
        }
        mock_api.post("/marketplace/listings/lst_abc123/purchase").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.marketplace.purchase(
            "lst_abc123", payment_method="x402", payment_proof="proof_data"
        )
        assert result.payment_method == "x402"

    def test_purchase_payment_required(self, client, mock_api) -> None:
        mock_api.post("/marketplace/listings/lst_abc123/purchase").mock(
            return_value=httpx.Response(402, json={"detail": "Insufficient credits"})
        )
        with pytest.raises(PaymentRequiredError):
            client.marketplace.purchase("lst_abc123", payment_method="credits")

    def test_get_revenue(self, client, mock_api) -> None:
        body = {
            "agent_id": "test-agent-01",
            "total_revenue_cents": 5000,
            "total_sales": 10,
            "listings_count": 3,
            "listings": [],
        }
        mock_api.get("/marketplace/revenue/me").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.marketplace.get_revenue()
        assert isinstance(result, RevenueResponse)
        assert result.total_revenue_cents == 5000
        assert result.total_sales == 10

    def test_get_credits(self, client, mock_api) -> None:
        body = {"agent_id": "test-agent-01", "credits_balance": 1500}
        mock_api.get("/marketplace/credits/me").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.marketplace.get_credits()
        assert isinstance(result, CreditsBalance)
        assert result.credits_balance == 1500

    def test_purchase_credits(self, client, mock_api) -> None:
        body = {
            "credits_added": 1000,
            "new_balance": 2500,
            "stripe_payment_intent_id": "pi_credits_123",
        }
        mock_api.post("/marketplace/credits/purchase").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.marketplace.purchase_credits(amount_cents=1000)
        assert result["credits_added"] == 1000

    def test_ingest_skills(self, client, mock_api) -> None:
        body = {"created": 3, "skipped": 1, "errors": 0}
        mock_api.post("/marketplace/ingest-skills").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.marketplace.ingest_skills(
            content="# Skills\n- Skill 1\n- Skill 2",
            file_type="skills_md",
        )
        assert result["created"] == 3


# ===========================================================================
# PaymentsResource
# ===========================================================================

class TestPaymentsResource:
    """Tests for client.payments methods."""

    def test_create_intent(self, client, mock_api) -> None:
        mock_api.post("/payments/create-intent").mock(
            return_value=httpx.Response(200, json=SAMPLE_PAYMENT_INTENT)
        )
        result = client.payments.create_intent(amount_cents=1000)
        assert isinstance(result, PaymentIntentResponse)
        assert result.payment_intent_id == "pi_test_123"
        assert result.client_secret == "pi_test_123_secret_abc"
        assert result.amount_cents == 1000
        assert result.platform_fee_cents == 50

    def test_create_intent_with_all_params(self, client, mock_api) -> None:
        mock_api.post("/payments/create-intent").mock(
            return_value=httpx.Response(200, json=SAMPLE_PAYMENT_INTENT)
        )
        result = client.payments.create_intent(
            amount_cents=1000,
            currency="eur",
            payment_type="skill_purchase",
            description="Test payment",
            metadata={"listing_id": "lst_123"},
        )
        assert isinstance(result, PaymentIntentResponse)

    def test_get_history(self, client, mock_api) -> None:
        body = {
            "payments": [
                {"payment_id": "pay_1", "amount_cents": 500, "status": "succeeded"},
                {"payment_id": "pay_2", "amount_cents": 300, "status": "pending"},
            ],
            "count": 2,
            "has_more": False,
        }
        mock_api.get("/payments/history").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.payments.get_history(limit=10)
        assert isinstance(result, PaymentHistoryResponse)
        assert result.count == 2
        assert len(result.payments) == 2
        assert result.has_more is False

    def test_get_history_empty(self, client, mock_api) -> None:
        body = {"payments": [], "count": 0, "has_more": False}
        mock_api.get("/payments/history").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.payments.get_history()
        assert result.count == 0
        assert result.payments == []


# ===========================================================================
# GraphResource
# ===========================================================================

class TestGraphResource:
    """Tests for client.graph methods."""

    def test_get_context(self, client, mock_api) -> None:
        body = {
            "agent_id": "agent-01",
            "context": "This agent specializes in testing.",
            "relationships": [{"type": "connection", "target": "agent-02"}],
        }
        mock_api.get("/graph/context/agent-01").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.graph.get_context("agent-01", query="testing")
        assert result["agent_id"] == "agent-01"

    def test_get_related(self, client, mock_api) -> None:
        body = {
            "related": [
                {"agent_id": "agent-02", "relationship": "connection"},
                {"agent_id": "agent-03", "relationship": "collaboration"},
            ],
            "count": 2,
        }
        mock_api.get("/graph/related/agent-01").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.graph.get_related("agent-01")
        assert result["count"] == 2

    def test_get_related_with_type_filter(self, client, mock_api) -> None:
        body = {"related": [{"agent_id": "agent-02", "relationship": "connection"}], "count": 1}
        mock_api.get("/graph/related/agent-01").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.graph.get_related("agent-01", relationship_type="connection")
        assert result["count"] == 1

    def test_enrich(self, client, mock_api) -> None:
        body = {"enrichment": "Agent is skilled in Python and testing frameworks."}
        mock_api.get("/graph/enrich/agent-01").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.graph.enrich("agent-01", task_context="code review")
        assert "Python" in result["enrichment"]

    def test_get_wallets(self, client, mock_api) -> None:
        body = {"wallets": [{"address": "0xabc", "network": "base"}], "count": 1}
        mock_api.get("/graph/wallet/agent-01").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.graph.get_wallets("agent-01")
        assert result["count"] == 1
        assert result["wallets"][0]["address"] == "0xabc"

    def test_store_memory(self, client, mock_api) -> None:
        body = {"success": True, "memory_id": "mem_123"}
        mock_api.post("/graph/memory").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.graph.store_memory(
            agent_id="agent-01",
            content="Learned about Python SDK testing.",
            metadata={"source": "sdk_test"},
        )
        assert result["success"] is True
        assert result["memory_id"] == "mem_123"

    def test_store_memory_without_metadata(self, client, mock_api) -> None:
        body = {"success": True, "memory_id": "mem_124"}
        mock_api.post("/graph/memory").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.graph.store_memory(
            agent_id="agent-01",
            content="A simple memory.",
        )
        assert result["success"] is True

    def test_search(self, client, mock_api) -> None:
        body = {
            "nodes": [{"type": "agent", "id": "agent-01"}],
            "facts": [{"statement": "agent-01 knows Python"}],
            "total_count": 2,
        }
        mock_api.get("/graph/search").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.graph.search("Python expertise", limit=5)
        assert result["total_count"] == 2
        assert len(result["nodes"]) == 1

    def test_get_context_forbidden(self, client, mock_api) -> None:
        mock_api.get("/graph/context/secret-agent").mock(
            return_value=httpx.Response(403, json={"detail": "Access denied"})
        )
        with pytest.raises(ForbiddenError):
            client.graph.get_context("secret-agent")


# ===========================================================================
# ToolsResource
# ===========================================================================

class TestToolsResource:
    """Tests for client.tools methods."""

    def test_list_available(self, client, mock_api) -> None:
        body = {
            "tools": [
                {"name": "retrieve_balance", "description": "Get Stripe balance"},
                {"name": "create_invoice", "description": "Create a Stripe invoice"},
            ],
            "count": 2,
        }
        mock_api.get("/tools/stripe/available").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.tools.list_available()
        assert result["count"] == 2
        assert result["tools"][0]["name"] == "retrieve_balance"

    def test_execute(self, client, mock_api) -> None:
        body = {
            "tool_name": "retrieve_balance",
            "success": True,
            "result": {"available": [{"amount": 10000, "currency": "usd"}]},
            "error": None,
        }
        mock_api.post("/tools/stripe/execute").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.tools.execute("retrieve_balance")
        assert result["success"] is True
        assert result["result"]["available"][0]["amount"] == 10000

    def test_execute_with_parameters(self, client, mock_api) -> None:
        body = {
            "tool_name": "create_invoice",
            "success": True,
            "result": {"invoice_id": "inv_123"},
            "error": None,
        }
        mock_api.post("/tools/stripe/execute").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.tools.execute(
            "create_invoice",
            parameters={"customer": "cus_abc", "amount": 5000},
        )
        assert result["result"]["invoice_id"] == "inv_123"

    def test_execute_rate_limited(self, client, mock_api) -> None:
        mock_api.post("/tools/stripe/execute").mock(
            return_value=httpx.Response(
                429,
                json={"detail": "Rate limit exceeded"},
                headers={"Retry-After": "30"},
            )
        )
        with pytest.raises(RateLimitError) as exc_info:
            client.tools.execute("retrieve_balance")
        assert exc_info.value.retry_after == 30.0

    def test_get_usage(self, client, mock_api) -> None:
        body = {
            "agent_id": "test-agent-01",
            "calls_this_minute": 5,
            "rate_limit_per_minute": 60,
            "calls_today": 150,
        }
        mock_api.get("/tools/stripe/usage").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.tools.get_usage()
        assert result["calls_this_minute"] == 5
        assert result["rate_limit_per_minute"] == 60

    def test_get_preferences(self, client, mock_api) -> None:
        body = {
            "preferred_currency": "usd",
            "auto_invoice": False,
            "disabled_tools": [],
        }
        mock_api.get("/tools/stripe/preferences").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.tools.get_preferences()
        assert result["preferred_currency"] == "usd"

    def test_update_preferences(self, client, mock_api) -> None:
        body = {
            "preferred_currency": "eur",
            "auto_invoice": True,
            "disabled_tools": ["create_refund"],
        }
        mock_api.patch("/tools/stripe/preferences").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.tools.update_preferences(
            {"preferred_currency": "eur", "auto_invoice": True}
        )
        assert result["preferred_currency"] == "eur"
        assert result["auto_invoice"] is True

    def test_get_optimization_hints(self, client, mock_api) -> None:
        body = {
            "hints": [
                {"hint": "Consider batching invoice creation", "priority": "medium"},
            ],
            "count": 1,
        }
        mock_api.get("/tools/stripe/optimization-hints").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = client.tools.get_optimization_hints()
        assert result["count"] == 1
        assert "batching" in result["hints"][0]["hint"]


# ===========================================================================
# ContractsResource
# ===========================================================================

class TestContractsResource:
    """Tests for client.contracts methods."""

    def test_create_sends_path_header_and_body(self, client, mock_api) -> None:
        route = mock_api.post("/contracts").mock(
            return_value=httpx.Response(200, json={"contract_id": "ct_1", "state": "draft"})
        )
        result = client.contracts.create(
            "Write a report", 500, require_approval=True, idempotency_key="key-12345678"
        )
        assert result["contract_id"] == "ct_1"
        request = route.calls.last.request
        assert request.headers["X-API-Key"] == TEST_API_KEY
        assert json.loads(request.content) == {
            "goal": "Write a report",
            "budget_cents": 500,
            "surface": "cli",
            "require_approval": True,
            "public": False,
            "idempotency_key": "key-12345678",
        }

    def test_id_is_url_encoded_like_the_typescript_client(self, client, mock_api) -> None:
        route = mock_api.get("/contracts/a%2Fb%3Fx").mock(return_value=httpx.Response(200, json={}))
        client.contracts.get("a/b?x")
        assert route.called

    def test_cancel(self, client, mock_api) -> None:
        route = mock_api.post("/contracts/ct_1/cancel").mock(
            return_value=httpx.Response(200, json={"contract_id": "ct_1", "state": "cancelled"})
        )
        assert client.contracts.cancel("ct_1")["state"] == "cancelled"
        assert route.called


# ===========================================================================
# Async resource tests (selected subset to verify async path)
# ===========================================================================

class TestAsyncAgentsResource:
    """Async tests for agents resource."""

    @pytest.mark.asyncio
    async def test_get_me(self, async_client, mock_api) -> None:
        mock_api.get("/agents/me").mock(
            return_value=httpx.Response(200, json=SAMPLE_AGENT_PROFILE)
        )
        result = await async_client.agents.get_me()
        assert isinstance(result, AgentPublicProfile)
        assert result.agent_id == "test-agent-01"

    @pytest.mark.asyncio
    async def test_register(self, async_client, mock_api) -> None:
        mock_api.post("/agents/register").mock(
            return_value=httpx.Response(200, json=SAMPLE_REGISTRATION_RESPONSE)
        )
        result = await async_client.agents.register(
            agent_id="new-agent-01",
            profile={"display_name": "NewBot"},
        )
        assert isinstance(result, AgentRegistrationResponse)
        assert result.agent_id == "new-agent-01"

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, async_client, mock_api) -> None:
        mock_api.get("/agents/missing").mock(
            return_value=httpx.Response(404, json={"detail": "Not found"})
        )
        with pytest.raises(NotFoundError):
            await async_client.agents.get("missing")

    @pytest.mark.asyncio
    async def test_heartbeat_hits_root_path(self, async_client, mock_api) -> None:
        body = {
            "status": "ok",
            "timestamp": "2026-04-26T12:00:00+00:00",
            "pending_connections": 0,
            "unread_messages": 0,
            "email": {"provisioned": False},
        }
        route = mock_api.post("/heartbeat").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = await async_client.agents.heartbeat()
        assert route.called
        assert result["status"] == "ok"
        assert result["email"]["provisioned"] is False


class TestAsyncMarketplaceResource:
    """Async tests for marketplace resource."""

    @pytest.mark.asyncio
    async def test_list_listings(self, async_client, mock_api) -> None:
        body = {"listings": [SAMPLE_LISTING], "count": 1}
        mock_api.get("/marketplace/listings").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = await async_client.marketplace.list_listings()
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_get_listing(self, async_client, mock_api) -> None:
        mock_api.get("/marketplace/listings/lst_abc123").mock(
            return_value=httpx.Response(200, json=SAMPLE_LISTING)
        )
        result = await async_client.marketplace.get_listing("lst_abc123")
        assert isinstance(result, MarketplaceListing)


class TestAsyncPaymentsResource:
    """Async tests for payments resource."""

    @pytest.mark.asyncio
    async def test_create_intent(self, async_client, mock_api) -> None:
        mock_api.post("/payments/create-intent").mock(
            return_value=httpx.Response(200, json=SAMPLE_PAYMENT_INTENT)
        )
        result = await async_client.payments.create_intent(amount_cents=1000)
        assert isinstance(result, PaymentIntentResponse)
        assert result.payment_intent_id == "pi_test_123"


class TestAsyncToolsResource:
    """Async tests for tools resource."""

    @pytest.mark.asyncio
    async def test_execute(self, async_client, mock_api) -> None:
        body = {
            "tool_name": "retrieve_balance",
            "success": True,
            "result": {"available": []},
            "error": None,
        }
        mock_api.post("/tools/stripe/execute").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = await async_client.tools.execute("retrieve_balance")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_rate_limited(self, async_client, mock_api) -> None:
        mock_api.post("/tools/stripe/execute").mock(
            return_value=httpx.Response(
                429,
                json={"detail": "Too fast"},
                headers={"Retry-After": "15"},
            )
        )
        with pytest.raises(RateLimitError) as exc_info:
            await async_client.tools.execute("retrieve_balance")
        assert exc_info.value.retry_after == 15.0


class TestAsyncWalletResource:
    """Async tests for wallet resource."""

    @pytest.mark.asyncio
    async def test_get_balance(self, async_client, mock_api) -> None:
        body = {"eth": "2.0", "usdc": "50.00"}
        mock_api.get("/wallet/me/balance").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = await async_client.wallet.get_balance()
        assert result["eth"] == "2.0"

    @pytest.mark.asyncio
    async def test_send(self, async_client, mock_api) -> None:
        body = {"tx_hash": "0xasync_hash", "status": "pending"}
        mock_api.post("/wallet/me/send").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = await async_client.wallet.send(
            to_address="0xrecipient", amount="5", asset="eth"
        )
        assert result["tx_hash"] == "0xasync_hash"


class TestAsyncGraphResource:
    """Async tests for graph resource."""

    @pytest.mark.asyncio
    async def test_search(self, async_client, mock_api) -> None:
        body = {"nodes": [], "facts": [], "total_count": 0}
        mock_api.get("/graph/search").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = await async_client.graph.search("nothing relevant")
        assert result["total_count"] == 0

    @pytest.mark.asyncio
    async def test_store_memory(self, async_client, mock_api) -> None:
        body = {"success": True, "memory_id": "mem_async_1"}
        mock_api.post("/graph/memory").mock(
            return_value=httpx.Response(200, json=body)
        )
        result = await async_client.graph.store_memory(
            agent_id="agent-01", content="Async memory test"
        )
        assert result["success"] is True
