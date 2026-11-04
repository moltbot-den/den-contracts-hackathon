"""
Contract engine: drives every live contract one idempotent step per tick
(hackathon PRD 5.2, docs/hackathon/D1-DATA-MODEL.md section 2).

A tick takes the lease on each active contract, then runs the step for its
state. Every step can be repeated after a crash: external calls are resumed
from ids stored on the contract or job (Wonder response ids, Network thread
ids, verifier run keys), and every state write goes through the store under
this instance's lease.

  draft       -> planning   (D3 escrows the buyer's credits here)
  planning    Wonder plans in the background; code validates the plan and budget
  hiring      jobs go out as Network `task`s to `cap:<capability>`
  running     answers come back, the judge checks them: a first fail gets a rework note,
              a second goes to a different worker (unpaid), a third fails the job
  verifying   Wonder assembles the passed jobs into the deliverable
  delivering  -> done       (D3 settles channels and refunds here)
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter

from services.contracts import judge as judge_mod
from services.contracts import marathon
from services.contracts.planner import (
    CONTRACTOR_MODEL,
    PlanRejected,
    assembly_request,
    assembly_text,
    parse_plan,
    plan_request,
)
from services.contracts.store import ConflictError, ContractStore, get_contract_store
from services.contracts.workers import CONTRACTOR_ID, COSTS

logger = logging.getLogger(__name__)

ACTIVE = ["draft", "planning", "hiring", "running", "verifying", "delivering"]
PENDING = ("queued", "in_progress")
MAX_MODEL_TRIES = 3  # plan or assembly attempts before the contract fails
MAX_HIRE_TRIES = 3
TASK_DEADLINE = timedelta(hours=1)  # the Network task's own expiry
JOB_DEADLINE = timedelta(minutes=10)  # no answer by then: send the task out again
MODEL_TIMEOUT = timedelta(minutes=10)  # a Wonder response still queued after this is abandoned
MAX_VERIFY_ERRORS = 10
MAX_START_ERRORS = 40  # failed Wonder creates (429s, outages) before the contract fails: about 10 min of ticks
DEP_CHARS = 20_000
TICK_SECONDS = 15
CONCURRENCY = 5

def _settled(job: Dict[str, Any]) -> bool:
    """Paid, or its payout was abandoned after repeated chain failures."""
    return bool(job.get("paid_cents") or job.get("payment_failed"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Engine:
    def __init__(self, store: Optional[ContractStore] = None, svc=None, meta=None, owner: Optional[str] = None,
                 channels="default"):
        self.store = store or get_contract_store()
        if svc is None:
            from services.net.service import get_network_service

            svc = get_network_service()
        if meta is None:
            from services.meta_client import get_meta_client

            meta = get_meta_client()
        # One owner per Engine, so two overlapping cron runs in one container are two lease holders.
        self.svc, self.meta, self.owner = svc, meta, owner or f"engine-{uuid.uuid4().hex[:8]}"
        self.leased: set = set()
        if channels == "default":
            from services.contracts.channels import get_channels

            channels = get_channels()
        self.channels = channels  # None: payouts wait until CDP is configured

    # ------------------------------------------------------------------ tick

    async def tick(self) -> Dict[str, int]:
        query = self.store.db.collection(self.store.fs._collection("contracts")).where(
            filter=FieldFilter("state", "in", ACTIVE)).limit(25)
        # ponytail: every active contract every tick (no next_poll_at index); fine
        # at hackathon volume, add the (state, next_poll_at) index past ~25 live.
        docs = [snap.to_dict() async for snap in query.stream()]
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(doc: Dict[str, Any]) -> bool:
            async with gate:
                if not await self.store.acquire_lease(doc["id"], self.owner):
                    return False
                self.leased.add(doc["id"])
                try:
                    await self.step(await self.store.get(doc["id"]))
                except ConflictError as e:
                    logger.info("contracts.step_conflict id=%s %s", doc["id"], e)
                except Exception:
                    logger.exception("contracts.step_failed id=%s", doc["id"])
                return True

        stepped = await asyncio.gather(*(one(d) for d in docs))
        return {"active": len(docs), "stepped": sum(stepped), "refunded": await self.settle()}

    async def settle(self) -> int:
        """Refund the unspent escrow of every closed contract still holding one."""
        query = self.store.db.collection(self.store.fs._collection("contracts")).where(
            filter=FieldFilter("escrow_state", "==", "held")).limit(50)
        refunded = 0
        async for snap in query.stream():
            doc = snap.to_dict()
            if doc["state"] in ("done", "failed", "cancelled"):
                try:
                    # Verified work is paid before the rest goes back, so the refund uses the final spend.
                    unpaid = [j for j in await self.store.jobs(doc["id"])
                              if j["state"] == "passed" and not _settled(j)]
                    if unpaid and self.channels is not None:
                        for job in unpaid:  # closed contracts are not leased; settle runs once per tick
                            await self._pay(doc, job, leased=False)
                        continue  # refund on a later tick, once spent_cents includes these payouts
                    refunded += (await self.store.refund(doc["id"])) is not None
                except Exception:
                    logger.exception("contracts.refund_failed id=%s", doc["id"])
        if self.channels is not None:
            try:
                await self.channels.finalize_due()
            except Exception as e:
                logger.warning("contracts.channel_finalize_failed err=%s", e)
        return refunded

    async def release(self) -> None:
        for contract_id in self.leased:
            try:
                await self.store.release_lease(contract_id, self.owner)
            except Exception as e:
                logger.warning("contracts.lease_release_failed id=%s %s", contract_id, e)  # it lapses in 60 s
        self.leased.clear()

    async def step(self, doc: Dict[str, Any]) -> None:
        if doc.get("kind") == marathon.KIND:
            return await marathon.step(self, doc)
        handler = {
            "draft": self._draft, "planning": self._planning, "hiring": self._work, "running": self._work,
            "verifying": self._verifying, "delivering": self._delivering,
        }.get(doc["state"])
        if handler:
            await handler(doc)

    # ------------------------------------------------------------- helpers

    async def _transition(self, doc, to_state, mutator=None, payload=None):
        return await self.store.transition(doc["id"], doc["state"], to_state, mutator, payload,
                                           lease_owner=self.owner)

    async def _job_to(self, doc, job, to_state, updates=None, payload=None) -> Dict[str, Any]:
        out = await self.store.transition_job(doc["id"], job["id"], job["state"], to_state,
                                              (lambda _j: updates) if updates else None, payload,
                                              lease_owner=self.owner)
        return {**out, "id": job["id"]}

    async def _event(self, doc, type, leased: bool = True, **kw) -> None:
        await self.store.append_event(doc["id"], type, lease_owner=self.owner if leased else None, **kw)

    async def _model_retry(self, doc, field: str, reason: str) -> None:
        """A failed or rejected Wonder response: start over, or fail the contract after MAX_MODEL_TRIES."""
        tries = doc.get(f"{field}_tries", 0) + 1
        if tries >= MAX_MODEL_TRIES:
            await self._transition(doc, "failed", lambda d: {"failure_reason": reason[:300]}, {"reason": reason[:300]})
        else:
            await self._event(doc, "error", updates={field: None, f"{field}_tries": tries},
                              payload={"reason": reason[:300]})

    # --------------------------------------------------------------- states

    async def _start(self, doc, field: str, request: Dict[str, Any], event: str) -> None:
        """Create a Wonder response into `doc[field]`. Persistent 429s or outages fail the contract (and refund it)."""
        from services.meta_client import MetaAPIError, SpendCapExceeded

        try:
            r = await self.meta.create_response(request)
        except (MetaAPIError, SpendCapExceeded) as e:
            errors = doc.get(f"{field}_start_errors", 0) + 1
            reason = f"Wonder unavailable: {e}"[:300]
            if errors >= MAX_START_ERRORS:
                await self._transition(doc, "failed", lambda d: {"failure_reason": reason}, {"reason": reason})
            else:
                await self._event(doc, "error", updates={f"{field}_start_errors": errors}, payload={"reason": reason})
            return
        await self._event(doc, event, updates={field: r["id"], f"{field}_at": _now()},
                          payload={"response_id": r["id"], "model": CONTRACTOR_MODEL})

    async def _draft(self, doc) -> None:
        await self.store.escrow(doc["id"], lease_owner=self.owner)

    async def _poll(self, doc, field: str) -> Optional[Dict[str, Any]]:
        """The finished Wonder response in `doc[field]`, or None while it runs (or after it was abandoned)."""
        from services.meta_client import MetaAPIError

        try:
            r = await self.meta.get_response(doc[field], model=CONTRACTOR_MODEL)
        except MetaAPIError as e:
            if 400 <= e.status_code < 500 and e.status_code != 429:
                await self._model_retry(doc, field, f"Wonder response unreadable ({e.status_code})")
                return None
            r = {"status": f"unreachable ({e.status_code})"}  # 429 or outage: wait, but not past the timeout
        if r.get("status") in PENDING or str(r.get("status")).startswith("unreachable"):
            started = doc.get(f"{field}_at")
            if started and _now() - started > MODEL_TIMEOUT:
                await self._model_retry(doc, field, f"Wonder response still {r.get('status')} after 10 min")
            return None
        return r

    async def _planning(self, doc) -> None:
        rid = doc.get("contractor_response_id")
        if not rid:
            return await self._start(doc, "contractor_response_id", plan_request(doc), "contractor.started")
        r = await self._poll(doc, "contractor_response_id")
        if r is None:
            return
        try:
            if r.get("status") != "completed":
                raise PlanRejected(f"Wonder response {r.get('status')}")
            plan = parse_plan(r, doc)
        except PlanRejected as e:
            await self._model_retry(doc, "contractor_response_id", f"plan rejected: {e}")
            return
        for job in plan["jobs"]:
            fields = {k: v for k, v in job.items() if k != "id"}
            try:
                await self.store.create_job(doc["id"], job["id"], owner_agent=doc["owner_agent"],
                                            plan_rev=plan["plan_rev"], **fields)
            except AlreadyExists:
                pass  # a retried step after a crash; the jobs are the same plan
        updates = {"plan_rev": plan["plan_rev"], "committed_cents": plan["committed_cents"],
                   "plan_summary": {"summary": plan["summary"], "jobs": len(plan["jobs"])}}
        payload = {"summary": plan["summary"], "jobs": [j["id"] for j in plan["jobs"]],
                   "committed_cents": plan["committed_cents"]}
        if doc.get("require_approval"):
            updates.update(pause_reason="awaiting_plan_approval", resume_state="hiring")
            await self._transition(doc, "paused", lambda d: updates, payload)
        else:
            await self._transition(doc, "hiring", lambda d: updates, payload)

    async def _work(self, doc) -> None:
        jobs = [j for j in await self.store.jobs(doc["id"]) if j.get("plan_rev") == doc["plan_rev"]]
        by_id = {j["id"]: j for j in jobs}
        for job in jobs:
            # ponytail: jobs run one after another inside a step; the lease is
            # renewed per job so a slow judge cannot let it lapse mid-step.
            if not await self.store.acquire_lease(doc["id"], self.owner):
                return
            if job["state"] == "pending" and all(by_id[d]["state"] == "passed" for d in job["depends_on"]):
                job = await self._job_to(doc, job, "hiring")
            if job["state"] == "hiring":
                job = await self._send_task(doc, job, by_id)
            if job["state"] == "running":
                job = await self._collect(doc, job)
            if job["state"] in ("submitted", "verifying"):
                job = await self._verify(doc, job)
            if job["state"] == "passed" and not _settled(job):
                job = await self._pay(doc, job)
            by_id[job["id"]] = job

        states = [j["state"] for j in by_id.values()]
        if doc["state"] == "hiring" and any(s not in ("pending", "hiring") for s in states):
            doc = await self._transition(doc, "running")
            await judge_mod.warm()
        failed = [j["id"] for j in by_id.values() if j["state"] == "failed"]
        if failed:
            # ponytail: D3 re-plans around a failed job (cap 3); D2 stops the contract.
            reason = f"job {failed[0]} failed"
            await self._transition(doc, "failed", lambda d: {"failure_reason": reason}, {"reason": reason})
            for job in by_id.values():  # stop the rest so workers and the judge stop spending
                if job["state"] not in ("passed", "failed", "cancelled"):
                    await self.store.transition_job(doc["id"], job["id"], job["state"], "cancelled")
        elif (states and all(s == "passed" for s in states) and doc["state"] == "running"
              and (self.channels is None or all(_settled(j) for j in by_id.values()))):
            await self._transition(doc, "verifying")

    async def _send_task(self, doc, job, by_id) -> Dict[str, Any]:
        from services.net.service import NetError

        if job.get("hire_tries", 0) >= MAX_HIRE_TRIES:  # every send so far went unanswered: stop re-sending
            reason = f"no worker answered after {MAX_HIRE_TRIES} tries"
            return await self._job_to(doc, job, "failed", {"failure_reason": reason}, {"reason": reason})

        deps = "".join(
            f"\n\nOutput of job {d} ({by_id[d]['capability']}):\n{(by_id[d].get('deliverable') or '')[:DEP_CHARS]}"
            for d in job["depends_on"]
        )
        subject = f"Contract {doc['id']}, job {job['id']}, attempt {job['attempt']} ({job['capability']})."
        sent = await self._sent_thread(subject)
        if sent:  # a crash after sending and before recording it: adopt that thread, never send twice
            return await self._job_to(doc, job, "running", {
                "thread_id": sent["thread_id"], "task_id": sent["thread_id"], "recipients": sent["recipients"],
                "deadline_at": _now() + JOB_DEADLINE}, {"thread_id": sent["thread_id"], "adopted": True})
        body = f"{subject}\n\n{job['instructions']}{deps}"
        try:
            sent = await self.svc.send(
                CONTRACTOR_ID, to=f"cap:{job['capability']}", type="task", body=body[:60_000],
                acceptance=job["acceptance"], deadline=_now() + TASK_DEADLINE, visibility="private",
                exclude=job.get("excluded_agents") or None,
            )
        except NetError as e:
            if e.message.startswith("rate limit"):
                return job  # the Contractor's per-minute send budget: wait for the next minute, not a failed hire
            tries = job.get("hire_tries", 0) + 1
            if tries >= MAX_HIRE_TRIES:
                return await self._job_to(doc, job, "failed", {"failure_reason": e.message[:300]},
                                          {"reason": e.message[:300]})
            await self._event(doc, "error", job_id=job["id"], job_updates={"hire_tries": tries},
                              payload={"reason": e.message[:300]})
            return {**job, "hire_tries": tries}
        recipients = [r["to"] for r in sent.get("recipients") or []] or [sent.get("to")]
        return await self._job_to(doc, job, "running", {
            "thread_id": sent["thread_id"], "task_id": sent["id"], "recipients": recipients,
            "deadline_at": _now() + JOB_DEADLINE,
        }, {"thread_id": sent["thread_id"], "recipients": recipients})

    async def _pay(self, doc, job, leased: bool = True) -> Dict[str, Any]:
        """Pay a passed job its budget over a payment channel; retried next tick on any chain error."""
        from google.cloud import firestore

        if self.channels is None:
            return job
        from services.contracts.channels import PayoutAbandoned

        try:
            ch = await self.channels.pay_job(doc["id"], job["id"], job["assigned_agent"], job["budget_cents"],
                                             consumer_agent=CONTRACTOR_ID)
        except PayoutAbandoned as e:
            # The worker got nothing, so the job's budget goes back to the buyer with the refund.
            # The event keeps the failure visible; the worker's claim can be paid by hand.
            await self._event(doc, "job.payment_failed", leased=leased, job_id=job["id"],
                              job_updates={"payment_failed": True}, payload={"reason": str(e)[:300]})
            return {**job, "payment_failed": True}
        except Exception as e:
            logger.warning("contracts.payout_failed id=%s job=%s err=%s", doc["id"], job["id"], e)
            return job
        paid = ch["claimed_cents"]
        await self._event(doc, "job.paid", leased=leased, job_id=job["id"],
                          job_updates={"paid_cents": paid, "channel_id": f"ch_{doc['id']}_{job['id']}"},
                          updates={"spent_cents": firestore.Increment(paid)},
                          payload={"paid_cents": paid, "worker": job["assigned_agent"], "chain_id": ch["chain_id"],
                                   "claim_tx": self.channels.link(ch["claim_tx"]),
                                   "open_tx": self.channels.link(ch["open_tx"])})
        return {**job, "paid_cents": paid}

    async def _sent_thread(self, subject: str) -> Optional[Dict[str, Any]]:
        from services.net.service import THREADS

        docs = await (self.svc._c(THREADS)
                      .where(filter=FieldFilter("opened_by", "==", CONTRACTOR_ID))
                      .where(filter=FieldFilter("subject", "==", subject[:140])).limit(1).get())
        return docs[0].to_dict() if docs else None

    async def _collect(self, doc, job) -> Dict[str, Any]:
        thread = await self.svc.thread(CONTRACTOR_ID, job["thread_id"])
        seen = set(job.get("seen_answers") or [])
        answer = next((m for m in thread["messages"]
                       if m.get("type") == "answer" and m.get("to_agent") == CONTRACTOR_ID
                       and m.get("from_agent") != CONTRACTOR_ID and m["id"] not in seen), None)
        if answer is None:
            if job.get("deadline_at") and job["deadline_at"] < _now():
                # The worker went quiet: send the task out again (counts as a hire try; _send_task stops at the cap).
                return await self._job_to(doc, job, "hiring", {
                    "thread_id": None, "attempt": job["attempt"] + 1, "hire_tries": job.get("hire_tries", 0) + 1,
                }, {"reason": "deadline passed with no answer"})
            return job
        # House workers meter each answer's Meta cost; outside agents bring their own compute and report none.
        cost = (await self.svc._c(COSTS).document(answer.get("reply_to") or "-").get()).to_dict() or {}
        pending = (job.get("pending_cost_cents") or 0) + -(-cost.get("cost_micro", 0) // 10_000)
        if pending > job["budget_cents"]:
            return await self._job_to(doc, job, "failed", {
                "pending_cost_cents": pending, "seen_answers": sorted(seen | {answer["id"]}),
                "failure_reason": f"metered cost {pending}¢ is over the job budget {job['budget_cents']}¢",
            }, {"agent": answer["from_agent"], "pending_cost_cents": pending})
        return await self._job_to(doc, job, "submitted", {
            "deliverable": answer.get("body") or "", "assigned_agent": answer["from_agent"],
            "answer_id": answer["id"], "seen_answers": sorted(seen | {answer["id"]}), "pending_cost_cents": pending,
        }, {"agent": answer["from_agent"], "answer_id": answer["id"], "pending_cost_cents": pending})

    async def _verify(self, doc, job) -> Dict[str, Any]:
        from services.meta_client import MetaAPIError, SpendCapExceeded
        from services.net.ask import record_verified_outcome

        if job["state"] == "submitted":
            job = await self._job_to(doc, job, "verifying")
        try:
            run = await judge_mod.judge(self.store.fs, self.meta, doc["id"], job)
        except (judge_mod.JudgeError, MetaAPIError, SpendCapExceeded) as e:
            # The judge's failure never counts against the worker; retry next tick, up to a cap.
            errors = job.get("verify_errors", 0) + 1
            if errors >= MAX_VERIFY_ERRORS:
                return await self._job_to(doc, job, "failed", {"failure_reason": f"verifier unavailable: {e}"[:300]})
            await self._event(doc, "error", job_id=job["id"], job_updates={"verify_errors": errors},
                              payload={"reason": f"verify: {e}"[:300]})
            return {**job, "verify_errors": errors}
        key = judge_mod.run_id(doc["id"], job["id"], job["attempt"])
        passed = run["verdict"] == "pass"
        await record_verified_outcome(self.svc, job["assigned_agent"], job["capability"], passed, key)
        verdicts = (job.get("verdicts") or []) + [{"attempt": job["attempt"], "verdict": run["verdict"],
                                                   "run_id": key, "at": _now()}]
        payload = {"verdict": run["verdict"], "run_id": key, "model": run["model"],
                   "failed": [c["text"] for c in run["criteria"] if not c["pass"]]}
        if passed:
            return await self._job_to(doc, job, "passed", {"verdicts": verdicts}, payload)
        fail_count = job.get("fail_count", 0) + 1
        if fail_count >= 3:
            return await self._job_to(doc, job, "failed", {"verdicts": verdicts, "fail_count": fail_count,
                                                           "failure_reason": "failed verification three times"},
                                      payload)
        if fail_count == 2:
            # Rehire: a different worker starts over in a new thread; the failed one is never paid.
            excluded = sorted(set(job.get("excluded_agents") or []) | {job["assigned_agent"]})
            return await self._job_to(doc, job, "hiring", {
                "verdicts": verdicts, "fail_count": fail_count, "attempt": job["attempt"] + 1,
                "excluded_agents": excluded, "thread_id": None, "assigned_agent": None, "hire_tries": 0,
            }, {**payload, "rehire": True, "excluded": excluded})
        feedback = "Your deliverable did not pass review. Fix these and send the full deliverable again:\n" + \
            "\n".join(f"- {c['text']}: {c['reason']}" for c in run["criteria"] if not c["pass"])
        if job.get("rework_note_for") != key:
            # Mark first, then send: a crash in between loses the note (the job deadline
            # then resends the task), but a retry can never send it twice.
            await self._event(doc, "progress", job_id=job["id"], job_updates={"rework_note_for": key},
                              payload={"rework": key})
            await self.svc.send(CONTRACTOR_ID, to=job["assigned_agent"], type="answer", body=feedback,
                                reply_to=job["answer_id"])
        return await self._job_to(doc, job, "running", {"verdicts": verdicts, "fail_count": fail_count,
                                                        "attempt": job["attempt"] + 1}, payload)

    async def _verifying(self, doc) -> None:
        rid = doc.get("assembly_response_id")
        if not rid:
            jobs = [j for j in await self.store.jobs(doc["id"])
                    if j.get("plan_rev") == doc["plan_rev"] and j["state"] == "passed"]
            return await self._start(doc, "assembly_response_id", assembly_request(doc, jobs), "assembly.started")
        r = await self._poll(doc, "assembly_response_id")
        if r is None:
            return
        text = assembly_text(r) if r.get("status") == "completed" else None
        if not text:
            await self._model_retry(doc, "assembly_response_id", f"assembly {r.get('status')} with no text")
            return
        # ponytail: D3 runs the verifier over the assembly against the buyer's hints.
        await self._transition(doc, "delivering", lambda d: {"deliverable": text},
                               {"chars": len(text)})

    async def _delivering(self, doc) -> None:
        if doc.get("require_approval") and not doc.get("delivery_review_requested"):
            await self._transition(doc, "paused", lambda d: {
                "pause_reason": "awaiting_delivery_approval", "resume_state": "delivering",
                "delivery_review_requested": True})
            return
        await self._transition(doc, "done")


async def run_for(seconds: float = 45.0) -> Dict[str, Any]:
    """Cron entry: house workers then the engine, every TICK_SECONDS, for about `seconds`."""
    from services.contracts.workers import serve_once

    engine = Engine()
    started, ticks, answered, stepped = time.monotonic(), 0, 0, 0
    try:
        while True:
            tick_start = time.monotonic()
            answered += sum((await serve_once(meta=engine.meta, svc=engine.svc)).values())
            stepped += (await engine.tick())["stepped"]
            ticks += 1
            if time.monotonic() - started + TICK_SECONDS > seconds:
                return {"ticks": ticks, "answered": answered, "stepped": stepped}
            await asyncio.sleep(max(0.0, TICK_SECONDS - (time.monotonic() - tick_start)))
    finally:
        await engine.release()
