"""
Contract store: the only writer of contract `state`, job `state` and events
(docs/hackathon/D1-DATA-MODEL.md sections 1 and 2).

Layout:
  contracts/{contract_id}
  contracts/{contract_id}/jobs/{plan_rev}-{nn}
  contracts/{contract_id}/events/{seq:010d}

Every write that changes state runs in one Firestore transaction that also
appends exactly one event and bumps `contracts.event_seq`, so `event_seq`
always equals the highest event id.

Events are hash-chained: each stores `prev_hash` and `hash`, where
`hash = sha256(prev_hash + canonical JSON of the event)`. The contract doc
keeps `last_hash`. Editing any past event breaks every hash after it, and
`verify_chain` reports the first bad seq.
"""

import hashlib
import json
import math
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from google.cloud import firestore as firestore_mod
from google.cloud.firestore_v1.base_query import FieldFilter

from services.dao.transactions import run_transaction

GENESIS = "0" * 64
LEASE = timedelta(seconds=60)

# from_state -> allowed to_states. Terminal states map to nothing.
CONTRACT_TRANSITIONS: Dict[str, set] = {
    "draft": {"planning", "cancelled", "failed"},
    "planning": {"hiring", "paused", "cancelled", "failed"},
    "paused": {"hiring", "delivering", "cancelled", "failed"},  # only to `resume_state`, or stop
    "hiring": {"running", "cancelled", "failed"},
    "running": {"planning", "verifying", "cancelled", "failed"},
    "verifying": {"planning", "delivering", "cancelled", "failed"},
    "delivering": {"paused", "done", "cancelled", "failed"},
}
JOB_TRANSITIONS: Dict[str, set] = {
    "pending": {"hiring", "cancelled"},
    "hiring": {"running", "failed", "cancelled"},
    "running": {"submitted", "hiring", "failed", "cancelled"},
    "submitted": {"verifying", "cancelled"},
    "verifying": {"passed", "running", "hiring", "failed", "cancelled"},
}
TERMINAL = {"done", "failed", "cancelled", "passed"}
STOP_STATES = {"cancelled", "failed"}

Mutator = Optional[Callable[[Dict[str, Any]], Dict[str, Any]]]


class IllegalTransition(Exception):
    pass


class ConflictError(Exception):
    """The doc is not in the expected state; re-read and decide again."""


class NotFound(Exception):
    pass


def _check_lease(doc: Dict[str, Any], lease_owner: Optional[str]) -> None:
    """Engine writes pass their lease owner; owner actions (router) pass None."""
    if lease_owner is None:
        return
    until = doc.get("lease_until")
    if doc.get("lease_owner") != lease_owner or not until or until <= datetime.now(timezone.utc):
        raise ConflictError(f"{lease_owner} does not hold the lease on {doc.get('id')}")


def event_hash(prev_hash: str, event: Dict[str, Any]) -> str:
    # ponytail: created_at is not hashed (Firestore timestamp round-trips are not
    # byte-stable); order and content are, via seq and the chain.
    body = {k: event.get(k) for k in ("seq", "type", "from_state", "to_state", "job_id", "contract_state", "payload")}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


class ContractStore:
    def __init__(self, fs=None):
        if fs is None:
            from services.firestore_client import get_firestore_client

            fs = get_firestore_client()
        self.fs = fs
        self.db = fs.db

    def _contract(self, contract_id: str):
        return self.db.collection(self.fs._collection("contracts")).document(contract_id)

    def _events(self, contract_id: str):
        return self._contract(contract_id).collection("events")

    def _job(self, contract_id: str, job_id: str):
        return self._contract(contract_id).collection("jobs").document(job_id)

    @staticmethod
    def _event(doc: Dict[str, Any], prev_hash: str, seq: int, **fields) -> Dict[str, Any]:
        event = {"seq": seq, "type": "contract.state", "from_state": None, "to_state": None, "job_id": None,
                 "contract_state": doc["state"], "payload": {}, **fields}
        event["prev_hash"] = prev_hash
        event["hash"] = event_hash(prev_hash, event)
        event["created_at"] = datetime.now(timezone.utc)
        return event

    def _append(self, tx, contract_id: str, doc: Dict[str, Any], updates: Dict[str, Any], **event_fields) -> None:
        """Write `updates` to the contract plus one chained event, inside `tx`."""
        seq = doc["event_seq"] + 1
        merged = {**doc, **updates}
        event = self._event(merged, doc["last_hash"], seq, **event_fields)
        tx.update(self._contract(contract_id), {
            **updates, "event_seq": seq, "last_hash": event["hash"], "updated_at": event["created_at"],
        })
        tx.create(self._events(contract_id).document(f"{seq:010d}"), event)

    async def create_contract(self, *, owner_agent: str, surface: str, goal: str, budget_cents: int,
                              require_approval: bool = False,
                              acceptance_hints: Optional[List[str]] = None,
                              evidence: Optional[List[str]] = None, public: bool = False,
                              kind: str = "contract", contract_id: Optional[str] = None) -> Dict[str, Any]:
        """`contract_id` given (an idempotency key's id): a second create with it raises AlreadyExists."""
        contract_id = contract_id or f"ct_{secrets.token_hex(8)}"
        now = datetime.now(timezone.utc)
        doc = {
            "id": contract_id, "owner_agent": owner_agent, "surface": surface, "goal": goal,
            "acceptance_hints": acceptance_hints or [], "evidence": evidence or [],
            "budget_cents": budget_cents, "reserve_cents": math.ceil(0.15 * budget_cents),
            "committed_cents": 0, "spent_cents": 0,
            "state": "draft", "pause_reason": None, "resume_state": None, "require_approval": require_approval,
            "plan_rev": 0, "escrow_state": "none", "public": public, "kind": kind,
            "lease_owner": None, "lease_until": None,
            "created_at": now, "updated_at": now, "deadline": None,
        }
        event = self._event(doc, GENESIS, 1, to_state="draft")
        doc.update(event_seq=1, last_hash=event["hash"])
        batch = self.db.batch()
        batch.create(self._contract(contract_id), doc)
        batch.create(self._events(contract_id).document(f"{1:010d}"), event)
        await batch.commit()
        return doc

    async def get(self, contract_id: str) -> Dict[str, Any]:
        snap = await self._contract(contract_id).get()
        if not snap.exists:
            raise NotFound(contract_id)
        return snap.to_dict()

    async def list_for_owner(self, owner_agent: str, limit: int = 50) -> List[Dict[str, Any]]:
        # ponytail: equality-only query (no composite index), sorted here; add an
        # (owner_agent, created_at desc) index if one owner ever has thousands.
        query = self.db.collection(self.fs._collection("contracts")).where(
            filter=FieldFilter("owner_agent", "==", owner_agent))
        docs = [snap.to_dict() async for snap in query.stream()]
        return sorted(docs, key=lambda d: d["created_at"], reverse=True)[:limit]

    async def jobs(self, contract_id: str) -> List[Dict[str, Any]]:
        return [{"id": snap.id, **snap.to_dict()} async for snap in self._contract(contract_id).collection("jobs").stream()]

    async def events(self, contract_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
        query = self._events(contract_id).where(filter=FieldFilter("seq", ">", after_seq)).order_by("seq")
        return [snap.to_dict() async for snap in query.stream()]

    async def transition(self, contract_id: str, expect_state: str, to_state: str,
                         mutator: Mutator = None, payload: Optional[Dict[str, Any]] = None,
                         lease_owner: Optional[str] = None) -> Dict[str, Any]:
        if to_state not in CONTRACT_TRANSITIONS.get(expect_state, set()):
            raise IllegalTransition(f"contract {expect_state} -> {to_state}")

        @firestore_mod.async_transactional
        async def _run(tx):
            snap = await self._contract(contract_id).get(transaction=tx)
            if not snap.exists:
                raise NotFound(contract_id)
            doc = snap.to_dict()
            _check_lease(doc, lease_owner)
            if doc["state"] != expect_state:
                raise ConflictError(f"contract {contract_id} is {doc['state']}, expected {expect_state}")
            updates = dict(mutator(doc) if mutator else {})
            if expect_state == "paused" and to_state not in STOP_STATES:
                if to_state != doc.get("resume_state"):
                    raise IllegalTransition(f"paused contract resumes to {doc.get('resume_state')}, not {to_state}")
                updates.update(pause_reason=None, resume_state=None)
            updates["state"] = to_state
            self._append(tx, contract_id, doc, updates,
                         from_state=expect_state, to_state=to_state, payload=payload or {})
            return {**doc, **updates}

        return await run_transaction(_run, self.db)

    async def append_event(self, contract_id: str, type: str, *, updates: Optional[Dict[str, Any]] = None,
                           payload: Optional[Dict[str, Any]] = None, job_id: Optional[str] = None,
                           job_updates: Optional[Dict[str, Any]] = None,
                           lease_owner: Optional[str] = None) -> None:
        """A non-state event (plan.created, progress, error), optionally with field updates in the same txn."""
        if updates and "state" in updates or job_updates and "state" in job_updates:
            raise IllegalTransition("state changes go through transition() or transition_job()")

        @firestore_mod.async_transactional
        async def _run(tx):
            snap = await self._contract(contract_id).get(transaction=tx)
            if not snap.exists:
                raise NotFound(contract_id)
            doc = snap.to_dict()
            _check_lease(doc, lease_owner)
            if job_updates and "paid_cents" in job_updates:
                # Two payers (a stepping engine and a settle run) can both finish the same payout;
                # only the first records it, or spent_cents counts it twice and the refund shrinks.
                job_snap = await self._job(contract_id, job_id).get(transaction=tx)
                if job_snap.to_dict().get("paid_cents"):
                    return
            if job_updates:
                tx.update(self._job(contract_id, job_id), {**job_updates, "updated_at": datetime.now(timezone.utc)})
            self._append(tx, contract_id, doc, dict(updates or {}), type=type, job_id=job_id, payload=payload or {})

        await run_transaction(_run, self.db)

    async def create_job(self, contract_id: str, job_id: str, **fields) -> None:
        now = datetime.now(timezone.utc)
        await self._job(contract_id, job_id).create({
            "contract_id": contract_id, "state": "pending", "attempt": 1, "fail_count": 0,
            "excluded_agents": [], "pending_cost_cents": 0, "paid_cents": 0, "verdicts": [],
            "created_at": now, "updated_at": now, **fields,
        })

    async def transition_job(self, contract_id: str, job_id: str, expect_state: str, to_state: str,
                             mutator: Mutator = None, payload: Optional[Dict[str, Any]] = None,
                             lease_owner: Optional[str] = None) -> Dict[str, Any]:
        if to_state not in JOB_TRANSITIONS.get(expect_state, set()):
            raise IllegalTransition(f"job {expect_state} -> {to_state}")

        @firestore_mod.async_transactional
        async def _run(tx):
            contract_snap = await self._contract(contract_id).get(transaction=tx)
            job_snap = await self._job(contract_id, job_id).get(transaction=tx)
            if not contract_snap.exists or not job_snap.exists:
                raise NotFound(f"{contract_id}/{job_id}")
            job = job_snap.to_dict()
            _check_lease(contract_snap.to_dict(), lease_owner)
            if contract_snap.get("state") in TERMINAL and to_state != "cancelled":
                raise ConflictError(f"contract {contract_id} is {contract_snap.get('state')}; jobs can only be cancelled")
            if job["state"] != expect_state:
                raise ConflictError(f"job {job_id} is {job['state']}, expected {expect_state}")
            updates = {**(mutator(job) if mutator else {}), "state": to_state,
                       "updated_at": datetime.now(timezone.utc)}
            tx.update(self._job(contract_id, job_id), updates)
            self._append(tx, contract_id, contract_snap.to_dict(), {}, type="job.state",
                         from_state=expect_state, to_state=to_state, job_id=job_id, payload=payload or {})
            return {**job, **updates}

        return await run_transaction(_run, self.db)

    async def verify_chain(self, contract_id: str) -> Dict[str, Any]:
        """Check every link, gap-free seqs, and that the log ends where the contract says it does."""
        doc = await self.get(contract_id)
        prev, count = GENESIS, 0
        async for snap in self._events(contract_id).order_by("__name__").stream():
            event = snap.to_dict()
            count += 1
            if (event.get("seq") != count or event.get("prev_hash") != prev
                    or event_hash(prev, event) != event.get("hash")):
                return {"valid": False, "first_bad_seq": count, "events": count}
            prev = event["hash"]
        if count != doc["event_seq"] or prev != doc["last_hash"]:
            # Events were deleted from the end (or the doc was edited).
            return {"valid": False, "first_bad_seq": count + 1, "events": count}
        return {"valid": True, "first_bad_seq": None, "events": count, "head": prev}

    async def reconcile(self, contract_id: str) -> bool:
        """Restart check: if the log drifted from the doc, the doc wins. Returns True if it appended."""

        @firestore_mod.async_transactional
        async def _run(tx):
            snap = await self._contract(contract_id).get(transaction=tx)
            if not snap.exists:
                raise NotFound(contract_id)
            doc = snap.to_dict()
            last = None
            async for snap in self._events(contract_id).order_by("seq", direction="DESCENDING").limit(1).stream(
                transaction=tx
            ):
                last = snap.to_dict()
            if last and last["seq"] == doc["event_seq"] and last["contract_state"] == doc["state"]:
                return False
            if (last["seq"] if last else 0) < doc["event_seq"]:
                # Events are missing. Appending would launder the gap into a valid-looking chain.
                raise ConflictError(f"contract {contract_id} log ends before event_seq {doc['event_seq']}")
            # Chain onto the log as it actually is, so verify_chain stays valid.
            base = {**doc, "event_seq": max(doc["event_seq"], last["seq"] if last else 0),
                    "last_hash": last["hash"] if last else GENESIS}
            self._append(tx, contract_id, base, {}, type="recovered",
                         from_state=last["contract_state"] if last else None, to_state=doc["state"])
            return True

        return await run_transaction(_run, self.db)

    async def acquire_lease(self, contract_id: str, owner: str, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)

        @firestore_mod.async_transactional
        async def _run(tx):
            snap = await self._contract(contract_id).get(transaction=tx)
            if not snap.exists:
                raise NotFound(contract_id)
            doc = snap.to_dict()
            until = doc.get("lease_until")
            if until and until > now and doc.get("lease_owner") != owner:
                return False
            # updated_at moves too, so tick (oldest-updated first) rotates through every active contract.
            tx.update(self._contract(contract_id), {"lease_owner": owner, "lease_until": now + LEASE,
                                                    "updated_at": now})
            return True

        return await run_transaction(_run, self.db)

    async def escrow(self, contract_id: str, lease_owner: Optional[str] = None) -> Dict[str, Any]:
        """draft -> planning with the buyer's budget taken from their credits in the same
        transaction, or draft -> failed when the balance is short. Exactly once by construction:
        a retry finds the contract no longer in draft."""
        from services.credits import prepare_spend

        @firestore_mod.async_transactional
        async def _run(tx):
            snap = await self._contract(contract_id).get(transaction=tx)
            if not snap.exists:
                raise NotFound(contract_id)
            doc = snap.to_dict()
            _check_lease(doc, lease_owner)
            if doc["state"] != "draft":
                raise ConflictError(f"contract {contract_id} is {doc['state']}, expected draft")
            write = await prepare_spend(tx, doc["owner_agent"], doc["budget_cents"],
                                        f"Contract {contract_id} budget escrow", key=f"contract_escrow_{contract_id}")
            if write is None:
                reason = f"insufficient credits for the {doc['budget_cents']}-credit budget"
                updates = {"state": "failed", "failure_reason": reason}
                self._append(tx, contract_id, doc, updates, from_state="draft", to_state="failed",
                             payload={"reason": reason})
                return {**doc, **updates}
            write()
            updates = {"state": "planning", "escrow_state": "held", "escrow_credits_cents": doc["budget_cents"]}
            self._append(tx, contract_id, doc, updates, from_state="draft", to_state="planning",
                         payload={"escrow_credits": doc["budget_cents"]})
            return {**doc, **updates}

        return await run_transaction(_run, self.db)

    async def refund(self, contract_id: str) -> Optional[int]:
        """Return the unspent escrow of a closed contract to the buyer, once. Returns the amount, or None."""
        from services.credits import add_credits_once

        doc = await self.get(contract_id)
        if doc["state"] not in TERMINAL or doc.get("escrow_state") != "held":
            return None
        amount = max(0, doc.get("escrow_credits_cents", 0) - doc.get("spent_cents", 0))
        if amount:
            # Keyed ledger row: a crash between this and the event below cannot refund twice.
            await add_credits_once(doc["owner_agent"], amount, f"Contract {contract_id} unspent budget",
                                   key=f"contract_refund_{contract_id}", tx_type="refund")

        @firestore_mod.async_transactional
        async def _mark(tx) -> bool:
            snap = await self._contract(contract_id).get(transaction=tx)
            current = snap.to_dict()
            if current.get("escrow_state") != "held":
                return False  # a concurrent settle already recorded it
            self._append(tx, contract_id, current, {"escrow_state": "refunded", "refund_credits_cents": amount},
                         type="escrow.refunded",
                         payload={"refund_credits": amount, "spent_cents": current.get("spent_cents", 0)})
            return True

        return amount if await run_transaction(_mark, self.db) else None

    async def release_lease(self, contract_id: str, owner: str) -> None:
        """Hand the contract back at the end of a run, so the next run need not wait out the lease."""

        @firestore_mod.async_transactional
        async def _run(tx):
            snap = await self._contract(contract_id).get(transaction=tx)
            if snap.exists and snap.get("lease_owner") == owner:
                tx.update(self._contract(contract_id), {"lease_owner": None, "lease_until": None})

        await run_transaction(_run, self.db)


def get_contract_store() -> ContractStore:
    # Cheap: wraps the shared Firestore client singleton, so no cache of its own.
    return ContractStore()
