"""
Worker payouts over TokenPaymentChannel (hackathon PRD 5.4).

What these tests pin down, and why each matters for money:
  - a passed job's payout goes approve -> open -> claim -> close request, and
    the claim is signed by the Contractor (consumer) and sent by the worker,
    as the contract requires;
  - a retry after a crash at any step never sends a transaction twice: done
    steps are skipped, and the step that crashed reuses its idempotency key;
  - a reverted transaction is retried under a new key, not replayed forever;
  - channels close only after the dispute window;
  - a step that keeps reverting (an expired or closed channel) is abandoned after
    three tries, because every reverted mainnet transaction burns gas.

Firestore is real (emulator); the chain is a fake that records transactions.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from services.contracts.channels import MAX_STEP_ATTEMPTS, Channels, ChannelError, PayoutAbandoned
from services.firestore_client import get_firestore_client

pytestmark = pytest.mark.emulator

CHANNEL_ID = "0x" + "ab" * 32


class FakeChain:
    def __init__(self, revert_once=None, crash_after=None, always_revert=None):
        self.sent, self.keys, self.revert_once, self.crash_after = [], [], revert_once, crash_after
        self.always_revert = always_revert

    async def ensure_gas(self, *wallets):
        pass

    async def send(self, sender, call, args, key):
        self.keys.append(key)
        if call == self.always_revert:
            return f"0xbad{len(self.keys)}", False, None
        if call == self.revert_once:
            self.revert_once = None
            return f"0xbad{len(self.keys)}", False, None
        tx = f"0x{abs(hash(key)) % 10**12:012d}"
        if tx not in [t for _, _, t in self.sent]:  # an idempotent resend returns the same tx, sends nothing new
            self.sent.append((call, sender["address"], tx))
        if call == self.crash_after:
            self.crash_after = None
            raise RuntimeError("instance died after sending")
        return tx, True, {"logs": []}

    def channel_id(self, receipt):
        return CHANNEL_ID

    async def sign_claim(self, consumer, channel_id, units, nonce):
        self.signed = (consumer["address"], channel_id, units, nonce)
        return "0x" + "11" * 65


async def _agent(address: str) -> str:
    agent_id = f"pay-{uuid4().hex[:6]}"
    await get_firestore_client().create_agent({"agent_id": agent_id, "status": "active",
                                               "cdp_wallet_id": f"w-{agent_id}", "cdp_wallet_address": address})
    return agent_id


async def _setup(chain):
    contractor, worker = await _agent("0xC0"), await _agent("0xW0")
    return Channels(get_firestore_client(), chain, 84532), contractor, worker, f"ct_{uuid4().hex[:8]}"


async def test_a_passed_job_is_opened_and_claimed_by_the_right_wallets():
    chain = FakeChain()
    ch, contractor, worker, cid = await _setup(chain)

    doc = await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)

    assert [c for c, _, _ in chain.sent] == ["approve", "openChannel", "submitClaim"]
    senders = {c: s for c, s, _ in chain.sent}
    assert senders["submitClaim"] == "0xW0" and senders["approve"] == senders["openChannel"] == "0xC0"
    # Sepolia scale: 600 cents -> 60,000 base units (1/100 of real USDC); the consumer signs nonce 1.
    assert chain.signed == ("0xC0", CHANNEL_ID, 60_000, 1)
    assert doc["state"] == "closing" and doc["claimed_cents"] == 600 and doc["onchain_id"] == CHANNEL_ID


async def test_a_crash_mid_payout_resumes_without_sending_anything_twice():
    chain = FakeChain(crash_after="submitClaim")
    ch, contractor, worker, cid = await _setup(chain)
    with pytest.raises(RuntimeError):
        await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)

    doc = await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)

    calls = [c for c, _, _ in chain.sent]
    assert calls == ["approve", "openChannel", "submitClaim"]
    # The resumed claim reused the crashed step's idempotency key, so CDP returns the same transaction.
    claim_keys = [k for k in chain.keys if ":claim:" in k]
    assert len(claim_keys) == 2 and len(set(claim_keys)) == 1
    assert doc["state"] == "closing"


async def test_a_reverted_claim_is_retried_under_a_new_key():
    chain = FakeChain(revert_once="submitClaim")
    ch, contractor, worker, cid = await _setup(chain)
    with pytest.raises(ChannelError):
        await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)
    await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)
    claim_keys = [k for k in chain.keys if ":claim:" in k]
    assert [k.rsplit(":", 1)[1] for k in claim_keys] == ["1", "2"]


async def test_channels_close_only_after_the_dispute_window(monkeypatch):
    from services.contracts import workers

    chain = FakeChain()
    ch, contractor, worker, cid = await _setup(chain)
    monkeypatch.setattr(workers, "CONTRACTOR_ID", contractor)
    await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)
    ref = ch._ref(f"ch_{cid}_1-01")

    assert await ch.finalize_due() == 0  # window still open
    await ref.update({"close_after": datetime.now(timezone.utc) - timedelta(seconds=1)})
    assert await ch.finalize_due() >= 1
    assert (await ref.get()).to_dict()["state"] == "closed"
    # The worker (provider) closes: the deployed contract refuses an early close by the consumer.
    assert chain.sent[-1][:2] == ("closeChannel", "0xW0")


async def test_a_refused_send_gets_a_new_key_because_cdp_caches_the_refusal():
    from services.contracts.channels import NotSent

    class RefuseOnce(FakeChain):
        async def send(self, sender, call, args, key):
            if call == "openChannel" and not getattr(self, "refused", False):
                self.refused = True
                self.keys.append(key)
                raise NotSent("unable to estimate gas")
            return await super().send(sender, call, args, key)

    chain = RefuseOnce()
    ch, contractor, worker, cid = await _setup(chain)
    with pytest.raises(NotSent):
        await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)
    await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)
    open_keys = [k for k in chain.keys if ":open:" in k]
    assert [k.rsplit(":", 1)[1] for k in open_keys] == ["1", "2"]


async def test_an_expired_channel_is_abandoned_after_three_reverted_claims():
    chain = FakeChain(always_revert="submitClaim")
    ch, contractor, worker, cid = await _setup(chain)
    for _ in range(MAX_STEP_ATTEMPTS):
        with pytest.raises(ChannelError):
            await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)
    for _ in range(2):
        with pytest.raises(PayoutAbandoned):
            await ch.pay_job(cid, "1-01", worker, 600, consumer_agent=contractor)
    assert len([k for k in chain.keys if ":claim:" in k]) == MAX_STEP_ATTEMPTS  # no gas spent after giving up
    assert (await ch._ref(f"ch_{cid}_1-01").get()).to_dict()["state"] == "failed"
