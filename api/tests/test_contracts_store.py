"""
Contract store: the only writer of contract state and events (hackathon PRD 5.2,
docs/hackathon/D1-DATA-MODEL.md section 2).

What these tests pin down, and why each matters:
  - only transitions in the tables are possible, and terminal states never move,
    so a bug elsewhere cannot jump a contract to `done` and skip settlement;
  - a transition with a stale expected state is refused, so two engine
    instances cannot both act on one contract;
  - every transition appends exactly one event in the same transaction, so
    `event_seq` always equals the last event id (SSE resume depends on it);
  - the event log is hash-chained, so editing any past event is detected;
  - after a crash the contract doc wins over a drifted log and a `recovered`
    event records it; a dead instance's lease lapses so another can take over.

Store behaviour runs against the Firestore emulator because the guarantees
live in Firestore transactions.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.contracts.store import (
    CONTRACT_TRANSITIONS,
    JOB_TRANSITIONS,
    ConflictError,
    ContractStore,
    IllegalTransition,
    TERMINAL,
    event_hash,
)

# ──────────────────────────────────────────────────────────────────────
# Tables and hashing (no Firestore)
# ──────────────────────────────────────────────────────────────────────


def test_terminal_states_have_no_way_out():
    for table in (CONTRACT_TRANSITIONS, JOB_TRANSITIONS):
        for state in TERMINAL:
            assert not table.get(state)


def test_done_is_reachable_only_from_delivering():
    # Delivering is where channels close and the refund runs; nothing may skip it.
    sources = {s for s, targets in CONTRACT_TRANSITIONS.items() if "done" in targets}
    assert sources == {"delivering"}


def test_a_job_is_paid_only_after_verification():
    sources = {s for s, targets in JOB_TRANSITIONS.items() if "passed" in targets}
    assert sources == {"verifying"}


def test_event_hash_covers_the_previous_hash_and_every_field():
    event = {"seq": 2, "type": "contract.state", "from_state": "draft", "to_state": "planning",
             "job_id": None, "payload": {"a": 1}}
    base = event_hash("0" * 64, event)
    assert event_hash("1" * 64, event) != base
    for key, value in (("seq", 3), ("to_state", "done"), ("payload", {"a": 2}), ("job_id", "1-01")):
        assert event_hash("0" * 64, {**event, key: value}) != base
    # Key order must not matter: the hash is over canonical JSON.
    assert event_hash("0" * 64, dict(reversed(list(event.items())))) == base


# ──────────────────────────────────────────────────────────────────────
# Store against the Firestore emulator
# ──────────────────────────────────────────────────────────────────────

pytestmark_emulator = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs the Firestore emulator (set FIRESTORE_EMULATOR_HOST or run scripts/test-api.sh)",
)


@pytest.fixture
def store():
    from google.cloud.firestore import AsyncClient

    prefix = f"ctstore{uuid.uuid4().hex[:8]}"
    fs = SimpleNamespace(db=AsyncClient(project="moltbot-den"), _collection=lambda n: f"{prefix}_{n}")
    return ContractStore(fs=fs)


async def _new(store, **overrides):
    fields = dict(owner_agent="agent-a", surface="cli", goal="Find a replacement part", budget_cents=2_000)
    fields.update(overrides)
    return await store.create_contract(**fields)


@pytestmark_emulator
async def test_create_writes_the_contract_and_event_one(store):
    contract = await _new(store)
    assert contract["state"] == "draft" and contract["event_seq"] == 1
    assert contract["reserve_cents"] == 300  # ceil(15% of budget)
    events = await store.events(contract["id"])
    assert [e["seq"] for e in events] == [1]
    assert events[0]["to_state"] == "draft"


@pytestmark_emulator
async def test_each_transition_appends_one_event_and_bumps_seq(store):
    c = await _new(store)
    await store.transition(c["id"], "draft", "planning")
    await store.transition(c["id"], "planning", "hiring", lambda doc: {"plan_rev": 1})
    doc = await store.get(c["id"])
    events = await store.events(c["id"])
    assert doc["state"] == "hiring" and doc["plan_rev"] == 1
    assert doc["event_seq"] == events[-1]["seq"] == 3
    assert [(e["from_state"], e["to_state"]) for e in events[1:]] == [("draft", "planning"), ("planning", "hiring")]


@pytestmark_emulator
async def test_illegal_transition_writes_nothing(store):
    c = await _new(store)
    with pytest.raises(IllegalTransition):
        await store.transition(c["id"], "draft", "done")
    assert (await store.get(c["id"]))["state"] == "draft"
    assert len(await store.events(c["id"])) == 1


@pytestmark_emulator
async def test_stale_expected_state_is_a_conflict(store):
    c = await _new(store)
    await store.transition(c["id"], "draft", "planning")
    with pytest.raises(ConflictError):
        await store.transition(c["id"], "draft", "planning")
    assert len(await store.events(c["id"])) == 2


@pytestmark_emulator
async def test_paused_resumes_only_to_its_resume_state(store):
    c = await _new(store)
    await store.transition(c["id"], "draft", "planning")
    await store.transition(c["id"], "planning", "paused",
                           lambda doc: {"pause_reason": "awaiting_plan_approval", "resume_state": "hiring"})
    with pytest.raises(IllegalTransition):
        await store.transition(c["id"], "paused", "delivering")
    await store.transition(c["id"], "paused", "hiring")
    doc = await store.get(c["id"])
    assert doc["state"] == "hiring" and doc["pause_reason"] is None and doc["resume_state"] is None


@pytestmark_emulator
async def test_terminal_contract_cannot_move(store):
    c = await _new(store)
    await store.transition(c["id"], "draft", "cancelled")
    with pytest.raises(IllegalTransition):
        await store.transition(c["id"], "cancelled", "planning")


@pytestmark_emulator
async def test_job_transitions_are_checked_and_logged(store):
    c = await _new(store)
    await store.create_job(c["id"], "1-01", capability="research", budget_cents=500)
    await store.transition_job(c["id"], "1-01", "pending", "hiring")
    with pytest.raises(IllegalTransition):
        await store.transition_job(c["id"], "1-01", "hiring", "passed")
    events = await store.events(c["id"])
    assert events[-1]["type"] == "job.state" and events[-1]["job_id"] == "1-01"
    assert (await store.get(c["id"]))["event_seq"] == events[-1]["seq"]


@pytestmark_emulator
async def test_chain_verifies_and_detects_an_edited_event(store):
    c = await _new(store)
    await store.transition(c["id"], "draft", "planning")
    await store.transition(c["id"], "planning", "hiring")
    assert (await store.verify_chain(c["id"]))["valid"] is True

    await store._events(c["id"]).document("0000000002").update({"to_state": "done"})
    result = await store.verify_chain(c["id"])
    assert result["valid"] is False and result["first_bad_seq"] == 2


@pytestmark_emulator
async def test_recovery_trusts_the_contract_doc_and_logs_it(store):
    c = await _new(store)
    await store.transition(c["id"], "draft", "planning")
    # Simulate a manual edit that left the log behind the doc.
    await store._contract(c["id"]).update({"state": "hiring"})
    assert await store.reconcile(c["id"]) is True
    events = await store.events(c["id"])
    assert events[-1]["type"] == "recovered" and events[-1]["to_state"] == "hiring"
    assert (await store.get(c["id"]))["event_seq"] == events[-1]["seq"]
    assert (await store.verify_chain(c["id"]))["valid"] is True
    assert await store.reconcile(c["id"]) is False  # consistent now, nothing appended


@pytestmark_emulator
async def test_lease_is_exclusive_until_it_lapses(store):
    c = await _new(store)
    now = datetime.now(timezone.utc)
    assert await store.acquire_lease(c["id"], "instance-a", now) is True
    assert await store.acquire_lease(c["id"], "instance-b", now + timedelta(seconds=30)) is False
    # instance-a died; 60 s later its lease has lapsed.
    assert await store.acquire_lease(c["id"], "instance-b", now + timedelta(seconds=61)) is True


@pytestmark_emulator
async def test_chain_detects_deleted_tail_events(store):
    c = await _new(store)
    await store.transition(c["id"], "draft", "planning")
    await store.transition(c["id"], "planning", "hiring")
    await store._events(c["id"]).document("0000000003").delete()
    result = await store.verify_chain(c["id"])
    assert result["valid"] is False
    # Recovery must not paper over missing events with a fresh chain link.
    with pytest.raises(ConflictError):
        await store.reconcile(c["id"])


@pytestmark_emulator
async def test_jobs_cannot_progress_after_the_contract_is_terminal(store):
    c = await _new(store)
    await store.create_job(c["id"], "1-01", capability="research", budget_cents=500)
    await store.transition_job(c["id"], "1-01", "pending", "hiring")
    await store.transition(c["id"], "draft", "cancelled")
    with pytest.raises(ConflictError):
        await store.transition_job(c["id"], "1-01", "hiring", "running")
    await store.transition_job(c["id"], "1-01", "hiring", "cancelled")  # cleanup is still allowed


@pytestmark_emulator
async def test_missing_contract_is_not_found_not_a_crash(store):
    from services.contracts.store import NotFound

    with pytest.raises(NotFound):
        await store.reconcile("ct_missing")
    with pytest.raises(NotFound):
        await store.acquire_lease("ct_missing", "instance-a")


@pytestmark_emulator
async def test_engine_transitions_need_the_lease(store):
    # Two engine instances must not both drive one contract: a transition made
    # on behalf of an instance whose lease lapsed or was taken over is refused.
    c = await _new(store)
    now = datetime.now(timezone.utc)
    assert await store.acquire_lease(c["id"], "instance-a", now - timedelta(seconds=61))
    assert await store.acquire_lease(c["id"], "instance-b")
    with pytest.raises(ConflictError):
        await store.transition(c["id"], "draft", "planning", lease_owner="instance-a")
    await store.create_job(c["id"], "1-01", capability="research", budget_cents=500)
    with pytest.raises(ConflictError):
        await store.transition_job(c["id"], "1-01", "pending", "hiring", lease_owner="instance-a")
    await store.transition(c["id"], "draft", "planning", lease_owner="instance-b")
    await store.transition_job(c["id"], "1-01", "pending", "hiring", lease_owner="instance-b")


@pytestmark_emulator
async def test_released_lease_is_free_at_once_but_only_the_holder_can_release(store):
    # Each cron run is a new lease holder; without release the next run waits 60 s.
    c = await _new(store)
    assert await store.acquire_lease(c["id"], "run-1")
    await store.release_lease(c["id"], "run-2")  # not the holder: no effect
    assert not await store.acquire_lease(c["id"], "run-2")
    await store.release_lease(c["id"], "run-1")
    assert await store.acquire_lease(c["id"], "run-2")
