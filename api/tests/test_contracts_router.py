"""
Contracts API skeleton (hackathon PRD 7, D1).

What these tests pin down, and why each matters:
  - a contract is visible only to its owner (others get 404, not 403, so ids
    do not leak), because contracts carry budgets and evidence;
  - the stream replays only events after `Last-Event-ID` and ends once the
    contract is terminal, so a reconnecting client never double-counts spend
    and a finished stream does not hold a connection open;
  - approve works only on a contract waiting for approval, and cancel stops
    any live contract, because both are money decisions by the owner;
  - the audit endpoint reports a valid hash chain.

Endpoint functions are called directly (httpx's ASGI transport buffers whole
streaming responses), against the Firestore emulator.
"""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs the Firestore emulator (set FIRESTORE_EMULATOR_HOST or run scripts/test-api.sh)",
)

OWNER = SimpleNamespace(agent_id="ct-owner")
STRANGER = SimpleNamespace(agent_id="ct-stranger")


async def _fund(*agents):
    """Contracts escrow the budget from credits, so test buyers need some."""
    from services.firestore_client import get_firestore_client

    db = get_firestore_client()
    for a in agents:
        await db.db.collection(db._collection("agents")).document(a.agent_id).set(
            {"agent_id": a.agent_id, "status": "active", "credits_balance": 1_000_000}, merge=True)


@pytest.fixture(autouse=True)
async def _funded_agents():
    await _fund(OWNER, STRANGER)


def _body(**overrides):
    from routers.contracts import ContractCreate

    fields = dict(goal="Identify this pump part and find a replacement", budget_cents=2_000, surface="cli")
    fields.update(overrides)
    return ContractCreate(**fields)


async def _frames(resp, n):
    out = []
    events = resp.body_iterator
    try:
        while len(out) < n:
            out.append(await asyncio.wait_for(events.__anext__(), timeout=10))
    finally:
        await events.aclose()
    return out


async def test_owner_sees_the_contract_and_strangers_get_404():
    from routers.contracts import create_contract, get_contract

    c = await create_contract(_body(), OWNER)
    assert c["state"] == "draft" and c["owner_agent"] == OWNER.agent_id
    assert (await get_contract(c["id"], OWNER))["id"] == c["id"]
    with pytest.raises(HTTPException) as exc:
        await get_contract(c["id"], STRANGER)
    assert exc.value.status_code == 404


async def test_list_returns_only_own_contracts_newest_first():
    from routers.contracts import create_contract, list_contracts

    agent = SimpleNamespace(agent_id=f"ct-lister-{os.urandom(3).hex()}")
    await _fund(agent)
    first = await create_contract(_body(), agent)
    second = await create_contract(_body(), agent)
    await create_contract(_body(), STRANGER)
    ids = [c["id"] for c in (await list_contracts(agent))["contracts"]]
    assert ids == [second["id"], first["id"]]


async def test_budget_above_the_float_cap_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _body(budget_cents=5_001)


async def test_stream_resumes_after_last_event_id_and_ends_when_terminal():
    from routers.contracts import cancel_contract, create_contract, stream_contract
    from services.contracts.store import get_contract_store

    c = await create_contract(_body(), OWNER)
    await get_contract_store().transition(c["id"], "draft", "planning")
    await cancel_contract(c["id"], OWNER)  # seq 3, terminal
    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=False)

    resp = await stream_contract(c["id"], request, OWNER, last_event_id="1")
    frames = await _frames(resp, 3)
    assert frames[0].startswith("id: 2\n") and "planning" in frames[0]
    assert frames[1].startswith("id: 3\n") and "cancelled" in frames[1]
    assert frames[2].startswith("event: end")


async def test_stream_is_owner_only():
    from routers.contracts import create_contract, stream_contract

    c = await create_contract(_body(), OWNER)
    with pytest.raises(HTTPException) as exc:
        await stream_contract(c["id"], MagicMock(), STRANGER, last_event_id=None)
    assert exc.value.status_code == 404


async def test_approve_only_when_waiting_for_approval():
    from routers.contracts import approve_contract, create_contract
    from services.contracts.store import get_contract_store

    store = get_contract_store()
    c = await create_contract(_body(require_approval=True), OWNER)
    with pytest.raises(HTTPException) as exc:
        await approve_contract(c["id"], OWNER)
    assert exc.value.status_code == 409

    await store.transition(c["id"], "draft", "planning")
    await store.transition(c["id"], "planning", "paused",
                           lambda d: {"pause_reason": "awaiting_plan_approval", "resume_state": "hiring"})
    with pytest.raises(HTTPException):
        await approve_contract(c["id"], STRANGER)
    assert (await approve_contract(c["id"], OWNER))["state"] == "hiring"


async def test_cancel_stops_a_live_contract_once():
    from routers.contracts import cancel_contract, create_contract

    c = await create_contract(_body(), OWNER)
    assert (await cancel_contract(c["id"], OWNER))["state"] == "cancelled"
    with pytest.raises(HTTPException) as exc:
        await cancel_contract(c["id"], OWNER)
    assert exc.value.status_code == 409


async def test_audit_reports_a_valid_chain():
    from routers.contracts import audit_contract, cancel_contract, create_contract

    c = await create_contract(_body(), OWNER)
    await cancel_contract(c["id"], OWNER)
    audit = await audit_contract(c["id"], OWNER)
    assert audit["valid"] is True and audit["events"] == 2


async def test_oversized_hints_and_foreign_evidence_are_rejected():
    from pydantic import ValidationError
    from routers.contracts import create_contract

    with pytest.raises(ValidationError):
        _body(acceptance_hints=["x" * 501])
    with pytest.raises(HTTPException) as exc:
        await create_contract(_body(evidence=["contracts/evidence/someone-else/crop.jpg"]), OWNER)
    assert exc.value.status_code == 400
    ok = await create_contract(_body(evidence=[f"contracts/evidence/{OWNER.agent_id}/crop.jpg"]), OWNER)
    assert ok["evidence"] == [f"contracts/evidence/{OWNER.agent_id}/crop.jpg"]


async def test_odd_last_event_id_is_ignored_not_a_500():
    from routers.contracts import cancel_contract, create_contract, stream_contract

    c = await create_contract(_body(), OWNER)
    await cancel_contract(c["id"], OWNER)
    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=False)
    frames = await _frames(await stream_contract(c["id"], request, OWNER, last_event_id="²"), 3)
    assert frames[0].startswith("id: 1\n")


async def test_streams_per_agent_are_capped():
    from routers import contracts as mod

    c = await mod.create_contract(_body(), OWNER)
    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=False)
    mod._open_streams[OWNER.agent_id] = mod.MAX_STREAMS_PER_AGENT
    try:
        with pytest.raises(HTTPException) as exc:
            await mod.stream_contract(c["id"], request, OWNER, last_event_id=None)
        assert exc.value.status_code == 429
    finally:
        mod._open_streams.pop(OWNER.agent_id, None)


async def test_a_budget_the_buyer_cannot_cover_is_refused_up_front():
    from routers.contracts import create_contract
    from services.firestore_client import get_firestore_client

    poor = SimpleNamespace(agent_id=f"ct-poor-{os.urandom(3).hex()}")
    db = get_firestore_client()
    await db.db.collection(db._collection("agents")).document(poor.agent_id).set(
        {"agent_id": poor.agent_id, "status": "active", "credits_balance": 1_999})
    with pytest.raises(HTTPException) as exc:
        await create_contract(_body(budget_cents=2_000), poor)
    assert exc.value.status_code == 402


async def test_only_public_contracts_are_visible_without_auth_and_never_their_evidence():
    from routers.contracts import public_contract

    private = await create_contract_for(_body())
    with pytest.raises(HTTPException) as exc:
        await public_contract(private["id"])
    assert exc.value.status_code == 404
    shown = await create_contract_for(_body(public=True, evidence=[f"contracts/evidence/{OWNER.agent_id}/crop.jpg"]))
    view = await public_contract(shown["id"])
    assert view["goal"] and view["events"][0]["to_state"] == "draft"
    assert "evidence" not in view and "owner_agent" not in view and "lease_owner" not in view


async def test_live_page_reports_totals_and_the_configured_marathon(monkeypatch):
    from routers import contracts as mod

    marathon = await create_contract_for(_body(public=True))
    monkeypatch.setattr(mod, "_live_cache", {})
    monkeypatch.setattr("config.get_settings", lambda: SimpleNamespace(marathon_contract_id=marathon["id"]))
    live = await mod.live_contracts()
    assert live["marathon"]["id"] == marathon["id"]
    assert set(live["totals"]) == {"contracts_done", "workers_paid", "paid_cents"}
    # Network numbers are bare counts: the page shows them without exposing any agent or message.
    assert set(live["network"]) == {"agents", "answers", "solutions_learned", "crews"}
    assert all(isinstance(v, int) for v in live["network"].values())


async def create_contract_for(body):
    from routers.contracts import create_contract

    return await create_contract(body, OWNER)


def test_public_view_shows_marathon_progress_but_never_leases_or_evidence():
    # The live page tells the marathon story from these fields; leases and evidence stay private.
    from routers.contracts import _public_view

    doc = {"id": "ct_m", "state": "running", "goal": "Marathon", "kind": "marathon", "children_started": 3,
           "children_done": 2, "children_failed": 1, "children_spent_cents": 340, "current_child": "ct_c3",
           "lease_owner": "engine-x", "evidence": ["contracts/evidence/a/x.jpg"]}
    events = [{"seq": 9, "type": "marathon.child_done", "payload": {
        "child": "ct_c2", "state": "done", "spent_cents": 170, "verifier_rejections": 1, "response_id": "resp_secret"}}]
    view = _public_view(doc, [], events)
    assert view["kind"] == "marathon" and view["children_done"] == 2 and view["current_child"] == "ct_c3"
    assert "lease_owner" not in view and "evidence" not in view
    assert view["events"][0]["payload"] == {"child": "ct_c2", "state": "done", "spent_cents": 170,
                                            "verifier_rejections": 1}


async def test_a_retried_create_with_the_same_key_returns_the_first_contract():
    """Muse may retry a create after a timeout; a second contract would escrow the budget twice."""
    from uuid import uuid4

    from routers.contracts import create_contract

    key = f"muse-{uuid4().hex}"
    first = await create_contract(_body(idempotency_key=key), OWNER)
    again = await create_contract(_body(idempotency_key=key, goal="something else"), OWNER)
    assert again["id"] == first["id"] and again["goal"] == first["goal"]
    other = await create_contract(_body(idempotency_key=key), STRANGER)  # keys are per owner
    assert other["id"] != first["id"]
    assert (await create_contract(_body(), OWNER))["id"] != first["id"]  # no key: always a new contract
