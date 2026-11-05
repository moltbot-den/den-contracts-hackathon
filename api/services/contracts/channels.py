"""
Worker payouts over TokenPaymentChannel on Base (hackathon PRD 5.4, D1-DATA-MODEL 1.4, 4.1).

A job is paid only after it passes verification. The Contractor wallet (the
channel consumer) opens one channel per paid job and signs one EIP-712 claim
for the payout; the worker's wallet submits it. After the contract's 1 h
dispute window `finalize_due` has the worker close the channel, which
transfers the payout (less the platform fee) to the worker and refunds the
rest of the deposit.

`payment_channels/ch_{contract_id}_{job_id}` is the state of record:

  opening -> open -> claimed -> closing -> closed

Every step is resumable after a crash. Each transaction is sent with a CDP
idempotency key derived from (channel doc, step, attempt), so a retry gets
the same transaction back instead of sending a second one; a reverted
transaction bumps the attempt and is sent again.

ponytail: one channel per job and gas paid by the sending wallets (testnet
ETH comes from the CDP faucet). Reuse a channel per worker and move gas to the
CDP paymaster if channel volume or mainnet gas ever matters.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from google.api_core.exceptions import AlreadyExists

logger = logging.getLogger(__name__)

COLLECTION = "payment_channels"
MAX_STEP_ATTEMPTS = 3  # tries per step before the payout is abandoned; each reverted try costs gas
CLOSE_WINDOW = timedelta(hours=1, minutes=2)  # contract DISPUTE_WINDOW plus a little block-time slack

# chain_id -> deployment. `units_per_cent` converts cents to USDC base units (6 decimals):
# mainnet pays real cents; Sepolia pays 1/100 scale because the CDP faucet hands out 1 USDC at a time.
CHAINS: Dict[int, Dict[str, Any]] = {
    8453: {"network": "base", "rpc": "https://mainnet.base.org", "units_per_cent": 10_000,
           "channel": "0x126B51241764f5af0638e40178760886b7d544aA",
           "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "explorer": "https://basescan.org/tx/"},
    84532: {"network": "base-sepolia", "rpc": "https://sepolia.base.org", "units_per_cent": 100,
            "channel": "0xA6Cd990f9bD940126BE9fa3cae309D31C5f35181",
            "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e", "explorer": "https://sepolia.basescan.org/tx/"},
}


class ChannelError(Exception):
    pass


class PayoutAbandoned(ChannelError):
    """A step kept failing (for example the channel expired or was closed): stop spending gas on it."""


class NotSent(ChannelError):
    """CDP refused the transaction before broadcasting it (for example gas estimation failed)."""


def _b(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str.removeprefix("0x"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Channels:
    def __init__(self, fs, chain, chain_id: int):
        self.fs, self.chain, self.chain_id = fs, chain, chain_id
        self.cfg = CHAINS[chain_id]

    def _ref(self, doc_id: str):
        return self.fs.db.collection(self.fs._collection(COLLECTION)).document(doc_id)

    def link(self, tx: str) -> str:
        return f"{self.cfg['explorer']}{tx}"

    async def _wallet(self, agent_id: str) -> Dict[str, str]:
        agent = await self.fs.get_agent(agent_id) or {}
        if not agent.get("cdp_wallet_id") or not agent.get("cdp_wallet_address"):
            raise ChannelError(f"{agent_id} has no CDP wallet")
        return {"name": agent["cdp_wallet_id"], "address": agent["cdp_wallet_address"]}

    async def _send(self, ref, doc: Dict[str, Any], step: str, sender: Dict[str, str], call: str, args: list):
        """Send one contract call for `step`, wait for it, return the receipt; a revert bumps the attempt."""
        attempt = doc.get("attempts", {}).get(step, 1)
        if attempt > MAX_STEP_ATTEMPTS:
            reason = f"{step} failed {MAX_STEP_ATTEMPTS} times: {doc.get('last_error', '')}"[:300]
            await ref.update({"state": "failed", "failure_reason": reason, "updated_at": _now()})
            raise PayoutAbandoned(reason)
        key = f"{ref.id}:{step}:{attempt}"
        try:
            tx, ok, receipt = await self.chain.send(sender, call, args, key)
        except NotSent as e:
            # CDP caches the refusal under the idempotency key, so the next try needs a new one.
            # A transaction that was broadcast keeps its key: retrying it returns the same tx.
            doc.setdefault("attempts", {})[step] = attempt + 1
            await ref.update({f"attempts.{step}": attempt + 1, "last_error": f"{step} not sent: {e}"[:300],
                              "updated_at": _now()})
            raise
        if not ok:
            await ref.update({f"attempts.{step}": attempt + 1, "last_error": f"{step} reverted: {tx}",
                              "updated_at": _now()})
            raise ChannelError(f"{step} reverted ({self.link(tx)})")
        return tx, receipt

    async def pay_job(self, contract_id: str, job_id: str, worker_agent: str, amount_cents: int,
                      consumer_agent: str) -> Dict[str, Any]:
        """Drive this job's payout until the claim is on chain and the close is requested. Idempotent."""
        ref = self._ref(f"ch_{contract_id}_{job_id}")
        consumer = await self._wallet(consumer_agent)
        try:
            worker = await self._wallet(worker_agent)
        except ChannelError as e:
            # No wallet never fixes itself in a retry loop: abandon so the budget is refunded and the contract moves on.
            raise PayoutAbandoned(str(e)) from e
        units = amount_cents * self.cfg["units_per_cent"]
        try:
            await ref.create({
                "contract_id": contract_id, "job_id": job_id, "worker_agent": worker_agent,
                "worker_wallet": worker["address"], "consumer_wallet": consumer["address"],
                "chain_id": self.chain_id, "deposit_cents": amount_cents, "deposit_units": units,
                "claimed_cents": 0, "claim_nonce": 0, "state": "opening", "attempts": {},
                "created_at": _now(), "updated_at": _now(),
            })
        except AlreadyExists:
            pass
        doc = (await ref.get()).to_dict()
        if doc["state"] == "failed":
            raise PayoutAbandoned(doc.get("failure_reason") or "payout abandoned")
        if doc["deposit_cents"] != amount_cents or doc["worker_wallet"] != worker["address"]:
            raise ChannelError(f"{ref.id} exists with different terms")
        await self.chain.ensure_gas(consumer, worker)

        if doc["state"] == "opening":
            approve_tx, _ = await self._send(ref, doc, "approve", consumer, "approve",
                                             [self.cfg["channel"], units])
            open_tx, receipt = await self._send(ref, doc, "open", consumer, "openChannel", [worker["address"], units])
            doc.update(state="open", onchain_id=self.chain.channel_id(receipt), approve_tx=approve_tx, open_tx=open_tx)
            await ref.update({k: doc[k] for k in ("state", "onchain_id", "approve_tx", "open_tx")} | {"updated_at": _now()})

        if doc["state"] == "open":
            signature = await self.chain.sign_claim(consumer, doc["onchain_id"], units, 1)
            claim_tx, _ = await self._send(ref, doc, "claim", worker, "submitClaim",
                                           [_b(doc["onchain_id"]), units, 0, 1, _b(signature)])
            doc.update(state="claimed", claimed_cents=amount_cents, claim_nonce=1, claim_tx=claim_tx)
            await ref.update({k: doc[k] for k in ("state", "claimed_cents", "claim_nonce", "claim_tx")}
                             | {"updated_at": _now()})

        if doc["state"] == "claimed":
            # The worker closes after the dispute window from its claim (finalize_due); the deployed
            # contract does not let the consumer request an early close (TimeoutNotReached).
            doc.update(state="closing", close_after=_now() + CLOSE_WINDOW)
            await ref.update({"state": "closing", "close_after": doc["close_after"], "updated_at": _now()})
        return doc

    async def finalize_due(self, limit: int = 25) -> int:
        """The worker closes each channel whose dispute window has passed; this moves the USDC to it."""
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = self.fs.db.collection(self.fs._collection(COLLECTION)).where(
            filter=FieldFilter("state", "==", "closing")).limit(limit)
        closed = 0
        async for snap in query.stream():
            doc = snap.to_dict()
            if doc.get("chain_id") != self.chain_id or doc["close_after"] > _now():
                continue
            try:
                worker = await self._wallet(doc["worker_agent"])
                close_tx, _ = await self._send(snap.reference, doc, "close", worker, "closeChannel",
                                               [_b(doc["onchain_id"])])
                await snap.reference.update({"state": "closed", "close_tx": close_tx, "updated_at": _now()})
                closed += 1
            except Exception as e:
                logger.warning("contracts.channel_close_failed id=%s err=%s", snap.id, e)
        return closed


class CdpChain:
    """The on-chain side: CDP server accounts send and sign, web3 reads receipts and balances."""

    ABI = [
        {"type": "function", "name": "approve", "stateMutability": "nonpayable", "outputs": [{"type": "bool"}],
         "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}]},
        {"type": "function", "name": "balanceOf", "stateMutability": "view", "outputs": [{"type": "uint256"}],
         "inputs": [{"name": "a", "type": "address"}]},
        {"type": "function", "name": "openChannel", "stateMutability": "nonpayable", "outputs": [{"type": "bytes32"}],
         "inputs": [{"name": "provider", "type": "address"}, {"name": "deposit", "type": "uint256"}]},
        {"type": "function", "name": "submitClaim", "stateMutability": "nonpayable", "outputs": [],
         "inputs": [{"name": "channelId", "type": "bytes32"}, {"name": "amount", "type": "uint256"},
                    {"name": "tokenCount", "type": "uint256"}, {"name": "nonce", "type": "uint256"},
                    {"name": "signature", "type": "bytes"}]},
        {"type": "function", "name": "closeChannel", "stateMutability": "nonpayable", "outputs": [],
         "inputs": [{"name": "channelId", "type": "bytes32"}]},
        {"type": "event", "name": "ChannelOpened", "anonymous": False,
         "inputs": [{"name": "channelId", "type": "bytes32", "indexed": True},
                    {"name": "consumer", "type": "address", "indexed": True},
                    {"name": "provider", "type": "address", "indexed": True},
                    {"name": "deposit", "type": "uint256", "indexed": False}]},
    ]
    MIN_GAS_WEI = 20_000_000_000_000  # 0.00002 ETH, a few dozen Base transactions

    def __init__(self, cdp_client, chain_id: int):
        from web3 import AsyncHTTPProvider, AsyncWeb3

        self.cdp, self.chain_id, self.cfg = cdp_client, chain_id, CHAINS[chain_id]
        self.w3 = AsyncWeb3(AsyncHTTPProvider(self.cfg["rpc"]))
        self.usdc = self.w3.eth.contract(address=self.w3.to_checksum_address(self.cfg["usdc"]), abi=self.ABI)
        self.channel = self.w3.eth.contract(address=self.w3.to_checksum_address(self.cfg["channel"]), abi=self.ABI)

    async def send(self, sender, call: str, args: list, key: str):
        from cdp.evm_transaction_types import TransactionRequestEIP1559
        from cdp.utils import create_deterministic_uuid_v4

        from cdp.openapi_client.errors import ApiError

        target = self.usdc if call == "approve" else self.channel
        try:
            tx = await self.cdp.evm.send_transaction(
                address=sender["address"], network=self.cfg["network"],
                transaction=TransactionRequestEIP1559(to=target.address, data=target.encode_abi(call, args), value=0),
                idempotency_key=create_deterministic_uuid_v4(key),
            )
        except ApiError as e:
            raise NotSent(f"{call}: {e.error_message}") from e
        receipt = await self.w3.eth.wait_for_transaction_receipt(tx, timeout=180)
        return tx, receipt.status == 1, receipt

    def channel_id(self, receipt) -> str:
        return "0x" + self.channel.events.ChannelOpened().process_receipt(receipt)[0]["args"]["channelId"].hex()

    async def sign_claim(self, consumer, channel_id: str, units: int, nonce: int) -> str:
        from cdp.openapi_client.models.eip712_domain import EIP712Domain

        return await self.cdp.evm.sign_typed_data(
            address=consumer["address"],
            domain=EIP712Domain(name="TokenPaymentChannel", version="1", chain_id=self.chain_id,
                                verifying_contract=self.channel.address),
            types={"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                    {"name": "chainId", "type": "uint256"},
                                    {"name": "verifyingContract", "type": "address"}],
                   "Claim": [{"name": "channelId", "type": "bytes32"}, {"name": "amount", "type": "uint256"},
                             {"name": "tokenCount", "type": "uint256"}, {"name": "nonce", "type": "uint256"}]},
            primary_type="Claim",
            message={"channelId": channel_id, "amount": units, "tokenCount": 0, "nonce": nonce},
        )

    async def ensure_gas(self, *wallets) -> None:
        """Testnet only: top up ETH (and the consumer's USDC) from the CDP faucet when low."""
        if self.chain_id != 84532:
            return
        for i, w in enumerate(wallets):
            if await self.w3.eth.get_balance(w["address"]) < self.MIN_GAS_WEI:
                await self.cdp.evm.request_faucet(address=w["address"], network=self.cfg["network"], token="eth")
            if i == 0 and await self.usdc.functions.balanceOf(w["address"]).call() < 500_000:
                await self.cdp.evm.request_faucet(address=w["address"], network=self.cfg["network"], token="usdc")


_channels: Optional[Channels] = None


def get_channels() -> Optional[Channels]:
    """The configured payout client, or None when CDP is not configured (payouts then wait)."""
    global _channels
    if _channels is None:
        from config import get_settings
        from services.firestore_client import get_firestore_client
        from services.wallet_provisioning_service import get_wallet_provisioning_service

        cdp = get_wallet_provisioning_service()._client
        if cdp is None:
            return None
        chain_id = get_settings().contracts_chain_id
        _channels = Channels(get_firestore_client(), CdpChain(cdp, chain_id), chain_id)
    return _channels
