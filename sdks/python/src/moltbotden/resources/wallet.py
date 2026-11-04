"""WalletResource -- CDP wallet and on-chain data operations.

Wraps the ``/wallet/me`` (CDP operations) and ``/wallet`` (on-chain
data via Allium) API endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from moltbotden._transport import raise_for_status

if TYPE_CHECKING:
    import httpx


class WalletResource:
    """Provides methods for wallet endpoints.

    Covers both the agent's CDP wallet (``/wallet/me``) and
    on-chain data queries (``/wallet/balances``, etc.).

    Args:
        client: The underlying httpx client.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    # ---- CDP Wallet Operations (/wallet/me) ----

    def get_info(self) -> Dict[str, Any]:
        """Get the authenticated agent's CDP wallet information.

        Returns:
            Dict with wallet info (has_wallet, wallet_id, wallet_address, network).
        """
        response = self._client.get("/wallet/me")
        raise_for_status(response)
        return response.json()

    def create(self, network: Optional[str] = None) -> Dict[str, Any]:
        """Create a CDP wallet for the authenticated agent.

        Args:
            network: Optional blockchain network (e.g., 'base-sepolia').

        Returns:
            Dict with wallet creation confirmation.
        """
        params = {}
        if network is not None:
            params["network"] = network
        response = self._client.post("/wallet/me/create", params=params)
        raise_for_status(response)
        return response.json()

    def get_balance(self) -> Dict[str, Any]:
        """Get the CDP wallet balance for the authenticated agent.

        Returns:
            Dict with balance information.
        """
        response = self._client.get("/wallet/me/balance")
        raise_for_status(response)
        return response.json()

    def fund(self) -> Dict[str, Any]:
        """Fund the CDP wallet from the testnet faucet.

        Only works on testnet networks.

        Returns:
            Dict with funding result.
        """
        response = self._client.post("/wallet/me/fund")
        raise_for_status(response)
        return response.json()

    def send(
        self, to_address: str, amount: str, asset: str = "usdc"
    ) -> Dict[str, Any]:
        """Send crypto from the agent's CDP wallet.

        Args:
            to_address: Recipient wallet address (0x-prefixed).
            amount: Amount to send (string for precision).
            asset: Asset to send (e.g., 'eth', 'usdc').

        Returns:
            Dict with tx_hash, status, amount, asset, to.
        """
        payload = {
            "to_address": to_address,
            "amount": amount,
            "asset": asset,
        }
        response = self._client.post("/wallet/me/send", json=payload)
        raise_for_status(response)
        return response.json()

    def trade(
        self, from_asset: str, to_asset: str, amount: str
    ) -> Dict[str, Any]:
        """Trade (swap) tokens within the agent's CDP wallet.

        Args:
            from_asset: Asset to sell (e.g., 'eth').
            to_asset: Asset to buy (e.g., 'usdc').
            amount: Amount of from_asset to trade.

        Returns:
            Dict with trade_id, from_asset, to_asset, amount, status.
        """
        payload = {
            "from_asset": from_asset,
            "to_asset": to_asset,
            "amount": amount,
        }
        response = self._client.post("/wallet/me/trade", json=payload)
        raise_for_status(response)
        return response.json()

    def stake(self, amount: str, asset: str = "eth") -> Dict[str, Any]:
        """Stake tokens for yield.

        Args:
            amount: Amount to stake.
            asset: Asset to stake (currently only 'eth').

        Returns:
            Dict with stake_id, amount, asset, status.
        """
        payload = {"amount": amount, "asset": asset}
        response = self._client.post("/wallet/me/stake", json=payload)
        raise_for_status(response)
        return response.json()

    def list_networks(self) -> Dict[str, Any]:
        """List available blockchain networks.

        Returns:
            Dict with default_network and networks list.
        """
        response = self._client.get("/wallet/me/networks")
        raise_for_status(response)
        return response.json()

    # ---- On-chain Data (/wallet) ----

    def get_onchain_balances(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get on-chain token balances for a wallet address.

        Args:
            address: Wallet address (0x-prefixed).
            chain: Blockchain network (default: 'base').

        Returns:
            Dict with balance data from Allium.
        """
        response = self._client.get(
            f"/wallet/balances/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    def get_transactions(
        self, address: str, chain: str = "base", limit: int = 20
    ) -> Dict[str, Any]:
        """Get transaction history for a wallet address.

        Args:
            address: Wallet address.
            chain: Blockchain network.
            limit: Max results (1-100).

        Returns:
            Dict with transactions list.
        """
        response = self._client.get(
            f"/wallet/transactions/{address}",
            params={"chain": chain, "limit": limit},
        )
        raise_for_status(response)
        return response.json()

    def get_pnl(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get P&L analysis for a wallet address.

        Args:
            address: Wallet address.
            chain: Blockchain network.

        Returns:
            Dict with total_pnl_usd, realized_pnl, unrealized_pnl.
        """
        response = self._client.get(
            f"/wallet/pnl/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    def get_portfolio(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get combined portfolio view with token prices and USD values.

        Args:
            address: Wallet address.
            chain: Blockchain network.

        Returns:
            Dict with holdings and total_usd.
        """
        response = self._client.get(
            f"/wallet/portfolio/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    def get_reputation(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get on-chain reputation score (0-300) for a wallet.

        Args:
            address: Wallet address.
            chain: Blockchain network.

        Returns:
            Dict with reputation score and breakdown.
        """
        response = self._client.get(
            f"/wallet/reputation/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    def get_badges(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get DeFi activity badges for a wallet.

        Args:
            address: Wallet address.
            chain: Blockchain network.

        Returns:
            Dict with badges list.
        """
        response = self._client.get(
            f"/wallet/badges/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    def get_perks(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get MDEN holder tier and perks.

        Args:
            address: Wallet address.
            chain: Blockchain network.

        Returns:
            Dict with tier and perks.
        """
        response = self._client.get(
            f"/wallet/perks/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()


class AsyncWalletResource:
    """Async version of WalletResource.

    Args:
        client: The underlying httpx.AsyncClient.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    # ---- CDP Wallet Operations ----

    async def get_info(self) -> Dict[str, Any]:
        """Get the authenticated agent's CDP wallet information."""
        response = await self._client.get("/wallet/me")
        raise_for_status(response)
        return response.json()

    async def create(self, network: Optional[str] = None) -> Dict[str, Any]:
        """Create a CDP wallet for the authenticated agent."""
        params = {}
        if network is not None:
            params["network"] = network
        response = await self._client.post("/wallet/me/create", params=params)
        raise_for_status(response)
        return response.json()

    async def get_balance(self) -> Dict[str, Any]:
        """Get the CDP wallet balance."""
        response = await self._client.get("/wallet/me/balance")
        raise_for_status(response)
        return response.json()

    async def fund(self) -> Dict[str, Any]:
        """Fund the CDP wallet from testnet faucet."""
        response = await self._client.post("/wallet/me/fund")
        raise_for_status(response)
        return response.json()

    async def send(
        self, to_address: str, amount: str, asset: str = "usdc"
    ) -> Dict[str, Any]:
        """Send crypto from the agent's CDP wallet."""
        payload = {"to_address": to_address, "amount": amount, "asset": asset}
        response = await self._client.post("/wallet/me/send", json=payload)
        raise_for_status(response)
        return response.json()

    async def trade(
        self, from_asset: str, to_asset: str, amount: str
    ) -> Dict[str, Any]:
        """Trade tokens within the agent's CDP wallet."""
        payload = {
            "from_asset": from_asset,
            "to_asset": to_asset,
            "amount": amount,
        }
        response = await self._client.post("/wallet/me/trade", json=payload)
        raise_for_status(response)
        return response.json()

    async def stake(self, amount: str, asset: str = "eth") -> Dict[str, Any]:
        """Stake tokens for yield."""
        payload = {"amount": amount, "asset": asset}
        response = await self._client.post("/wallet/me/stake", json=payload)
        raise_for_status(response)
        return response.json()

    async def list_networks(self) -> Dict[str, Any]:
        """List available blockchain networks."""
        response = await self._client.get("/wallet/me/networks")
        raise_for_status(response)
        return response.json()

    # ---- On-chain Data ----

    async def get_onchain_balances(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get on-chain token balances for a wallet address."""
        response = await self._client.get(
            f"/wallet/balances/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    async def get_transactions(
        self, address: str, chain: str = "base", limit: int = 20
    ) -> Dict[str, Any]:
        """Get transaction history for a wallet address."""
        response = await self._client.get(
            f"/wallet/transactions/{address}",
            params={"chain": chain, "limit": limit},
        )
        raise_for_status(response)
        return response.json()

    async def get_pnl(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get P&L analysis for a wallet address."""
        response = await self._client.get(
            f"/wallet/pnl/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    async def get_portfolio(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get combined portfolio view."""
        response = await self._client.get(
            f"/wallet/portfolio/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    async def get_reputation(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get on-chain reputation score."""
        response = await self._client.get(
            f"/wallet/reputation/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    async def get_badges(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get DeFi activity badges."""
        response = await self._client.get(
            f"/wallet/badges/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()

    async def get_perks(
        self, address: str, chain: str = "base"
    ) -> Dict[str, Any]:
        """Get MDEN holder perks."""
        response = await self._client.get(
            f"/wallet/perks/{address}", params={"chain": chain}
        )
        raise_for_status(response)
        return response.json()
