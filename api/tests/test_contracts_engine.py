"""
Contract engine end to end (hackathon PRD 5.2, 5.3; D2 evidence).

What these tests pin down, and why each matters:
  - a Wonder plan becomes jobs that go out over `cap:` routing to the house
    workers, whose answers are judged and assembled into one deliverable, so
    the whole hire-verify-deliver path works with real Network routing;
  - a failed verdict sends the worker one rework note and pays nothing for the
    failed attempt; the reworked deliverable is judged again;
  - verdicts teach routing even though Will owns every house worker;
  - a plan over budget never creates jobs: the budget rule is code, not model;
  - a step never runs without the lease;
  - failure injection (PRD 10): Wonder 429s and outages, and payouts that keep
    reverting, all end in a consistent state: the contract finishes or fails, the
    buyer gets back every unpaid cent exactly once, and nothing is paid twice.

Firestore and the Network service are real (emulator); Meta is scripted.
"""

import json

import pytest

from services.contracts import engine as engine_mod, workers
from services.contracts.engine import Engine
from services.contracts.store import ContractStore
from services.firestore_client import get_firestore_client
from services.net import autonomy
from services.net import service as net
from services.net.ask import STATS
from services.net.service import NetworkService

pytestmark = pytest.mark.emulator

PLAN = {"summary": "Identify the part, source it, write the fix.", "jobs": [
    {"capability": "vision", "instructions": "Identify the pump part.", "acceptance": ["names the part"],
     "budget_cents": 300, "depends_on": [], "deliverable_type": "text"},
    {"capability": "research", "instructions": "Find a replacement and price.",
     "acceptance": ["gives a part number", "gives a price"], "budget_cents": 400, "depends_on": [0],
     "deliverable_type": "text"},
    {"capability": "writing", "instructions": "Write replacement steps.", "acceptance": ["numbered steps"],
     "budget_cents": 300, "depends_on": [1], "deliverable_type": "markdown"},
]}


class FakeMeta:
    """Wonder plans and assembles in the background; Spark works and judges."""

    def __init__(self, plan=PLAN, fail_first_judgment_of=None, fail_judgments=1):
        self.plan, self.fail_once, self.fail_n = plan, set(fail_first_judgment_of or []), fail_judgments
        self.judged = []

    async def create_response(self, payload):
        return {"id": "resp_plan" if "text" in payload else "resp_asm", "status": "queued"}

    async def get_response(self, response_id, *, model):
        text = json.dumps(self.plan) if response_id == "resp_plan" else "# Final answer\nAll three jobs combined."
        return {"id": response_id, "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}

    async def create_chat_completion(self, payload, *, bucket=None):
        if "response_format" not in payload:  # a worker doing its job
            last = payload["messages"][-1]["content"]
            return {"choices": [{"message": {"content": f"Deliverable for: {last[:80]}"}}]}
        user = payload["messages"][-1]["content"]
        job = user.split("\n", 1)[0]
        self.judged.append(job)
        n = user.split("Acceptance criteria:\n", 1)[1].split("\n\nDeliverable:", 1)[0].count("\n") + 1
        ok = not (job in self.fail_once and self.judged.count(job) <= self.fail_n)
        verdict = {"criteria": [{"index": i, "pass": ok, "reason": "checked"} for i in range(n)]}
        return {"choices": [{"message": {"content": json.dumps(verdict)}}]}


@pytest.fixture(autouse=True)
def _fresh_denscores():
    net._denscore_cache.clear()


async def _house(svc):
    db = get_firestore_client()
    for agent_id, (cap, _) in {workers.CONTRACTOR_ID: ("contracting", ""), **workers.HOUSE_WORKERS}.items():
        if not await db.get_agent(agent_id):
            await db.create_agent({"agent_id": agent_id, "status": "active",
                                   "owner": {"firebase_uid": "will"},
                                   "cdp_wallet_id": f"w-{agent_id}", "cdp_wallet_address": f"0x{agent_id}",
                                   "profile": {"capabilities": {"primary_functions": [cap]}}})
    if not await autonomy.crews_of(svc, workers.CONTRACTOR_ID):
        await autonomy.create_crew(svc, workers.CONTRACTOR_ID, workers.HOUSE_CREW)
        code = (await autonomy.invite(svc, workers.CONTRACTOR_ID, workers.HOUSE_CREW))["invite_code"]
        for agent_id in workers.HOUSE_WORKERS:
            await autonomy.join(svc, agent_id, workers.HOUSE_CREW, code)
    for agent_id in workers.HOUSE_WORKERS:
        # As provisioned in prod (scripts/hackathon/provision_house_agents.py); the default 20 holds the rest.
        await autonomy.set_policy(svc, agent_id, {"max_tasks_per_day": 1000})


def _store() -> ContractStore:
    """A private contracts collection, so a tick sees only this test's contracts."""
    from types import SimpleNamespace
    from uuid import uuid4

    real, prefix = get_firestore_client(), f"cteng{uuid4().hex[:8]}"
    return ContractStore(fs=SimpleNamespace(
        db=real.db, _collection=lambda name: f"{prefix}_{name}" if name == "contracts" else real._collection(name)))


async def _buyer(name: str, credits: int = 10_000) -> str:
    """A funded buyer agent with a fresh id (credits are escrowed at plan time)."""
    from uuid import uuid4

    agent_id = f"{name}-{uuid4().hex[:6]}"
    await get_firestore_client().create_agent({"agent_id": agent_id, "status": "active", "credits_balance": credits})
    return agent_id


async def _balance(agent_id: str) -> int:
    db = get_firestore_client()
    snap = await db.db.collection(db._collection("agents")).document(agent_id).get()
    return snap.to_dict().get("credits_balance", 0)


async def _run(engine, svc, meta, contract_id, ticks=20):
    for _ in range(ticks):
        await workers.serve_once(svc=svc, meta=meta)
        await engine.tick()
        if (await engine.store.get(contract_id))["state"] in ("done", "failed", "paused"):
            break
    return await engine.store.get(contract_id)


async def test_planned_contract_is_hired_verified_reworked_and_delivered():
    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    from services.contracts.channels import Channels
    from tests.test_contracts_channels import FakeChain

    meta = FakeMeta(fail_first_judgment_of={"Task: Find a replacement and price."})
    chain = FakeChain()
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test",
                    channels=Channels(get_firestore_client(), chain, 84532))
    c = await store.create_contract(owner_agent=await _buyer("buyer-1"), surface="cli", goal="Fix my pool pump", budget_cents=2_000)

    final = await _run(engine, svc, meta, c["id"])

    assert final["state"] == "done", final.get("failure_reason")
    assert final["deliverable"].startswith("# Final answer")
    assert final["committed_cents"] + final["reserve_cents"] <= final["budget_cents"]
    jobs = {j["capability"]: j for j in await store.jobs(c["id"])}
    assert {j["state"] for j in jobs.values()} == {"passed"}
    assert {j["assigned_agent"] for j in jobs.values()} == {"den-vision", "den-research", "den-writer"}
    # The research job failed once, was reworked in the same thread, then passed on attempt 2.
    research = jobs["research"]
    assert [v["verdict"] for v in research["verdicts"]] == ["fail", "pass"] and research["attempt"] == 2
    thread = await svc.thread(workers.CONTRACTOR_ID, research["thread_id"])
    assert [m["type"] for m in thread["messages"]] == ["task", "answer", "answer", "answer"]
    # Same-owner workers still learn from verdicts: one fail and one pass for research.
    stats = (await svc._c(STATS).document("den-research__research").get()).to_dict()
    assert (stats["alpha"], stats["beta"]) == (2.0, 2.0)
    # Every verified job was paid its budget over a channel, signed by the Contractor, claimed by the worker.
    assert {j["paid_cents"] for j in jobs.values()} == {300, 400}
    assert [c for c, _, _ in chain.sent].count("submitClaim") == 3
    await engine.settle()
    closed = await store.get(c["id"])
    assert closed["spent_cents"] == 1_000 and closed["refund_credits_cents"] == 1_000
    assert (await store.verify_chain(c["id"]))["valid"] is True
    states = [e["to_state"] for e in await store.events(c["id"]) if e["type"] == "contract.state"]
    assert states == ["draft", "planning", "hiring", "running", "verifying", "delivering", "done"]


async def test_plan_over_budget_creates_no_jobs_and_fails_after_retries():
    svc, store = NetworkService(get_firestore_client()), _store()
    greedy = {**PLAN, "jobs": [{**PLAN["jobs"][0], "budget_cents": 1_900}]}
    engine = Engine(store=store, svc=svc, meta=FakeMeta(plan=greedy), owner="engine-test")
    c = await store.create_contract(owner_agent=await _buyer("buyer-2"), surface="cli", goal="Too greedy", budget_cents=2_000)

    final = await _run(engine, svc, engine.meta, c["id"], ticks=10)

    assert final["state"] == "failed" and "spendable" in final["failure_reason"]
    assert await store.jobs(c["id"]) == []


async def test_no_step_without_the_lease():
    svc, store = NetworkService(get_firestore_client()), _store()
    c = await store.create_contract(owner_agent=await _buyer("buyer-3"), surface="cli", goal="Leased", budget_cents=2_000)
    assert await store.acquire_lease(c["id"], "someone-else")
    await Engine(store=store, svc=svc, meta=FakeMeta(), owner="engine-test").tick()
    assert (await store.get(c["id"]))["state"] == "draft"


async def test_a_busy_house_worker_is_not_throttled_by_the_pair_loop_guard():
    # The marathon sends one worker many jobs within minutes. The pair loop guard
    # (13 messages in 10 min) would refuse its answers and the work would be lost;
    # Contractor threads are bounded by code, so they are exempt.
    svc = NetworkService(get_firestore_client())
    await _house(svc)
    tasks = []
    for n in range(14):
        tasks.append(await svc.send(workers.CONTRACTOR_ID, to="den-coder", type="task", body=f"job {n}",
                                    acceptance=["works"]))
    for t in tasks[:2]:
        await svc.send("den-coder", to=workers.CONTRACTOR_ID, type="answer", body="done", reply_to=t["id"])


async def test_a_job_whose_worker_goes_quiet_is_sent_out_again():
    from datetime import datetime, timedelta, timezone

    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    meta = FakeMeta()
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test")
    c = await store.create_contract(owner_agent=await _buyer("buyer-4"), surface="cli", goal="Quiet worker", budget_cents=2_000)
    for agent_id in workers.HOUSE_WORKERS:  # online, but they never answer
        await svc.register_session(agent_id, runtime="hosted", session_key=f"house:{agent_id}", name=workers.SESSION)
    for _ in range(4):  # draft -> planning -> plan -> task sent; no worker pass, so nobody answers
        await engine.tick()
    job = next(j for j in await store.jobs(c["id"]) if j["state"] == "running")
    await store._job(c["id"], job["id"]).update({"deadline_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    await engine.tick()  # running -> hiring
    await engine.tick()  # task sent again
    resent = next(j for j in await store.jobs(c["id"]) if j["id"] == job["id"])
    assert resent["state"] == "running" and resent["attempt"] == 2 and resent["thread_id"] != job["thread_id"]


async def test_a_job_nobody_answers_fails_after_the_hire_tries_instead_of_looping():
    """A task every worker drops (Meta's filter refused it) once went out every 10 minutes forever."""
    from datetime import datetime, timedelta, timezone

    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    engine = Engine(store=store, svc=svc, meta=FakeMeta(), owner="engine-test")
    c = await store.create_contract(owner_agent=await _buyer("buyer-quiet"), surface="cli", goal="Nobody answers", budget_cents=2_000)
    for agent_id in workers.HOUSE_WORKERS:
        await svc.register_session(agent_id, runtime="hosted", session_key=f"house:{agent_id}", name=workers.SESSION)
    for _ in range(4):
        await engine.tick()
    job_id = next(j for j in await store.jobs(c["id"]) if j["state"] == "running")["id"]
    for _ in range(engine_mod.MAX_HIRE_TRIES):
        await store._job(c["id"], job_id).update({"deadline_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
        await engine.tick()
        await engine.tick()
    job = next(j for j in await store.jobs(c["id"]) if j["id"] == job_id)
    assert job["state"] == "failed" and "no worker answered" in job["failure_reason"]
    assert job["attempt"] == engine_mod.MAX_HIRE_TRIES + 1  # the first send, then one per missed deadline


async def test_a_task_meta_refuses_is_dropped_but_an_outage_is_kept():
    from services.meta_client import MetaAPIError

    class Refusing(FakeMeta):
        def __init__(self, status):
            super().__init__()
            self.status, self.calls = status, 0

        async def create_chat_completion(self, payload, *, bucket=None):
            self.calls += 1
            raise MetaAPIError("Meta API returned %d" % self.status, status_code=self.status)

    svc = NetworkService(get_firestore_client())
    await _house(svc)
    for status, kept in ((400, False), (503, True), (429, True)):
        sent = await svc.send(workers.CONTRACTOR_ID, to="den-research", type="task", body=f"Find filters {status}",
                              acceptance=["Names the filter"])
        meta = Refusing(status)
        await workers.serve_once(svc=svc, meta=meta)
        inbox = [m["id"] for m in await svc.inbox("den-research", mark_read=False)]
        assert (sent["id"] in inbox) is kept, status
        for m in inbox:  # clear for the next case
            await svc.ack("den-research", [m], "read")


async def test_a_task_sent_before_a_crash_is_adopted_not_sent_twice():
    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    for agent_id in workers.HOUSE_WORKERS:
        await svc.register_session(agent_id, runtime="hosted", session_key=f"house:{agent_id}", name=workers.SESSION)
    engine = Engine(store=store, svc=svc, meta=FakeMeta(), owner="engine-test")
    c = await store.create_contract(owner_agent=await _buyer("buyer-5"), surface="cli", goal="Crash once", budget_cents=2_000)
    for _ in range(3):  # draft -> planning -> plan stored; first job is pending
        await engine.tick()
    real_send = svc.send
    sends = []

    async def send_then_crash(*args, **kwargs):
        sends.append(await real_send(*args, **kwargs))
        raise RuntimeError("instance died after sending")

    svc.send = send_then_crash
    await engine.tick()  # the task goes out, the job write never happens
    svc.send = real_send
    await engine.tick()
    job = next(j for j in await store.jobs(c["id"]) if j["id"].endswith("-01"))
    assert len(sends) == 1 and job["state"] == "running" and job["thread_id"] == sends[0]["thread_id"]



async def test_budget_is_escrowed_once_and_the_unspent_part_refunded_once():
    # The buyer's budget leaves their credits when planning starts and whatever
    # was not paid out comes back at close; neither move can happen twice.
    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    buyer = await _buyer("buyer-6", credits=5_000)
    engine = Engine(store=store, svc=svc, meta=FakeMeta(), owner="engine-test")
    c = await store.create_contract(owner_agent=buyer, surface="cli", goal="Escrow me", budget_cents=2_000)

    await engine.tick()  # draft -> planning, escrow held
    held = await store.get(c["id"])
    assert held["state"] == "planning" and held["escrow_state"] == "held"
    assert await _balance(buyer) == 3_000

    # The buyer cancels after $7 of verified work was paid out.
    await store.transition(c["id"], "planning", "cancelled", lambda d: {"spent_cents": 700})
    for _ in range(2):
        await engine.settle()
    closed = await store.get(c["id"])
    assert closed["escrow_state"] == "refunded" and closed["refund_credits_cents"] == 1_300
    assert await _balance(buyer) == 4_300


async def test_a_buyer_without_the_budget_never_starts_planning():
    svc, store = NetworkService(get_firestore_client()), _store()
    buyer = await _buyer("buyer-7", credits=500)
    engine = Engine(store=store, svc=svc, meta=FakeMeta(), owner="engine-test")
    c = await store.create_contract(owner_agent=buyer, surface="cli", goal="Too poor", budget_cents=2_000)
    await engine.tick()
    failed = await store.get(c["id"])
    assert failed["state"] == "failed" and "credits" in failed["failure_reason"]
    assert await _balance(buyer) == 500 and failed["escrow_state"] == "none"



async def test_a_second_fail_rehires_a_different_worker_and_pays_the_first_nothing():
    from uuid import uuid4

    from services.contracts.channels import Channels
    from tests.test_contracts_channels import FakeChain

    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    rival = f"ext-research-{uuid4().hex[:6]}"  # an external research agent, online but slow to answer
    await get_firestore_client().create_agent({"agent_id": rival, "status": "active",
                                               "profile": {"capabilities": {"primary_functions": ["research"]}}})
    await svc.register_session(rival, runtime="openclaw", name="desk", status="idle")
    chain = FakeChain()
    meta = FakeMeta(fail_first_judgment_of={"Task: Find a replacement and price."}, fail_judgments=2)
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test",
                    channels=Channels(get_firestore_client(), chain, 84532))
    c = await store.create_contract(owner_agent=await _buyer("buyer-8"), surface="cli", goal="Rehire", budget_cents=2_000)

    for _ in range(20):
        await workers.serve_once(svc=svc, meta=meta)
        await engine.tick()
        research = next((j for j in await store.jobs(c["id"]) if j["capability"] == "research"), {})
        if research.get("fail_count") == 2 and research["state"] == "running":
            break

    assert [v["verdict"] for v in research["verdicts"]] == ["fail", "fail"]
    assert research["excluded_agents"] == ["den-research"] and research["attempt"] == 3
    thread = await svc._c("net_threads").document(research["thread_id"]).get()
    assert thread.to_dict()["recipients"] == [rival]  # the failed worker is routed around
    assert not research.get("paid_cents")
    assert "0xden-research" not in [sender for call, sender, _ in chain.sent if call == "submitClaim"]


class FlakyWonder(FakeMeta):
    """Wonder answers 429 on the first `create_429s` creates and on the first `poll_429s` polls."""

    def __init__(self, create_429s=0, poll_429s=0, **kw):
        super().__init__(**kw)
        self.create_429s, self.poll_429s, self.creates = create_429s, poll_429s, 0

    async def create_response(self, payload):
        from services.meta_client import MetaAPIError

        self.creates += 1
        if self.create_429s:
            self.create_429s -= 1
            raise MetaAPIError("rate limited", status_code=429)
        return await super().create_response(payload)

    async def get_response(self, response_id, *, model):
        from services.meta_client import MetaAPIError

        if self.poll_429s:
            self.poll_429s -= 1
            raise MetaAPIError("rate limited", status_code=429)
        return await super().get_response(response_id, model=model)


async def test_wonder_429s_are_ridden_out_and_the_contract_still_finishes():
    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    buyer = await _buyer("buyer-429", credits=5_000)
    meta = FlakyWonder(create_429s=3, poll_429s=3)
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test")
    c = await store.create_contract(owner_agent=buyer, surface="cli", goal="Rate limited", budget_cents=2_000)

    final = await _run(engine, svc, meta, c["id"], ticks=30)

    assert final["state"] == "done", final.get("failure_reason")
    events = await store.events(c["id"])
    assert [e["type"] for e in events].count("contractor.started") == 1  # one plan, not one per retry
    assert sum(1 for e in events if e["type"] == "error" and "Wonder unavailable" in e["payload"]["reason"]) == 3
    await engine.settle()
    assert await _balance(buyer) == 5_000  # no channels: nothing paid, the whole budget came back once


async def test_a_wonder_outage_fails_the_contract_and_refunds_the_buyer(monkeypatch):
    from services.contracts import engine as engine_mod

    monkeypatch.setattr(engine_mod, "MAX_START_ERRORS", 3)
    svc, store = NetworkService(get_firestore_client()), _store()
    buyer = await _buyer("buyer-outage", credits=5_000)
    meta = FlakyWonder(create_429s=10**6)
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test")
    c = await store.create_contract(owner_agent=buyer, surface="cli", goal="Outage", budget_cents=2_000)

    for _ in range(6):
        await engine.tick()
    for _ in range(2):
        await engine.settle()

    final = await store.get(c["id"])
    assert final["state"] == "failed" and "Wonder unavailable" in final["failure_reason"]
    assert meta.creates == 3  # it stopped asking once it gave up
    assert final["escrow_state"] == "refunded" and await _balance(buyer) == 5_000


async def test_a_wonder_response_unreachable_past_the_timeout_is_abandoned(monkeypatch):
    from datetime import timedelta

    from services.contracts import engine as engine_mod

    monkeypatch.setattr(engine_mod, "MODEL_TIMEOUT", timedelta(0))
    svc, store = NetworkService(get_firestore_client()), _store()
    meta = FlakyWonder(poll_429s=10**6)
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test")
    c = await store.create_contract(owner_agent=await _buyer("buyer-poll"), surface="cli", goal="Poll", budget_cents=2_000)

    for _ in range(3):  # escrow, start the plan, poll (429 past the timeout: abandon)
        await engine.tick()

    doc = await store.get(c["id"])
    assert doc["contractor_response_id"] is None and doc["contractor_response_id_tries"] == 1


async def test_payouts_that_keep_reverting_are_abandoned_and_refunded_not_retried_forever():
    from services.contracts.channels import MAX_STEP_ATTEMPTS, Channels
    from tests.test_contracts_channels import FakeChain

    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    buyer = await _buyer("buyer-expired", credits=5_000)
    meta, chain = FakeMeta(), FakeChain(always_revert="submitClaim")
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test",
                    channels=Channels(get_firestore_client(), chain, 84532))
    c = await store.create_contract(owner_agent=buyer, surface="cli", goal="Expired channel", budget_cents=2_000)

    final = await _run(engine, svc, meta, c["id"], ticks=40)
    for _ in range(3):
        await engine.settle()

    assert final["state"] == "done", final.get("failure_reason")
    jobs = await store.jobs(c["id"])
    assert all(j["payment_failed"] and not j.get("paid_cents") for j in jobs)
    assert len([k for k in chain.keys if ":claim:" in k]) == MAX_STEP_ATTEMPTS * len(jobs)
    closed = await store.get(c["id"])
    assert closed["spent_cents"] == 0 and closed["refund_credits_cents"] == 2_000
    assert await _balance(buyer) == 5_000
    assert sum(e["type"] == "job.payment_failed" for e in await store.events(c["id"])) == len(jobs)


async def test_the_marathon_runs_children_one_after_another_and_logs_each_outcome():
    # The marathon never holds money itself: each child escrows and refunds its own budget,
    # and every child's outcome lands in the marathon's event log.
    from datetime import datetime, timedelta, timezone

    from services.contracts import marathon

    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    buyer = await _buyer("buyer-marathon", credits=5_000)
    meta = FakeMeta()
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test")
    m = await store.create_contract(owner_agent=buyer, surface="web", goal="Marathon", budget_cents=100,
                                    public=True, kind=marathon.KIND)
    await store.append_event(m["id"], "test.config", updates={"child_budget_cents": 2_000})  # PLAN costs $10

    for _ in range(30):
        await workers.serve_once(svc=svc, meta=meta)
        await engine.tick()
        if (await store.get(m["id"])).get("children_done"):
            break

    doc = await store.get(m["id"])
    assert doc["state"] == "running" and doc["escrow_state"] == "none"
    assert doc["children_started"] == 1 and doc["children_done"] == 1 and doc["current_child"] is None
    done = [e for e in await store.events(m["id"]) if e["type"] == "marathon.child_done"]
    assert done[0]["payload"]["state"] == "done" and done[0]["payload"]["jobs"] == 3
    child = await store.get(done[0]["payload"]["child"])
    assert child["public"] and child["goal"] == marathon.GOALS[0]
    assert await _balance(buyer) == 5_000  # the child's escrow came back in full (no channels in this test)

    await engine.tick()
    assert (await store.get(m["id"]))["children_started"] == 1  # not due yet: one child per interval
    await store.append_event(m["id"], "test.clock", updates={
        "last_child_at": datetime.now(timezone.utc) - timedelta(minutes=marathon.DEFAULT_EVERY_MINUTES + 1)})
    await engine.tick()
    doc = await store.get(m["id"])
    assert doc["children_started"] == 2 and (await store.get(doc["current_child"]))["goal"] == marathon.GOALS[1]
    assert (await store.verify_chain(m["id"]))["valid"] is True


async def test_a_rate_limited_send_waits_for_the_next_minute_instead_of_failing_the_job():
    # Under load the Contractor hits the network's 60-per-minute send limit. That refusal is
    # temporary, so it must not use up the job's hire tries (three of them would fail the job).
    from services.net.service import NetError

    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    meta = FakeMeta()
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test")
    real_send, refusals = svc.send, {"left": 5}

    async def busy_send(*a, **kw):
        if refusals["left"]:
            refusals["left"] -= 1
            raise NetError(429, "rate limit: 60 messages per minute")
        return await real_send(*a, **kw)

    svc.send = busy_send
    c = await store.create_contract(owner_agent=await _buyer("buyer-busy"), surface="cli", goal="Busy", budget_cents=2_000)

    final = await _run(engine, svc, meta, c["id"], ticks=30)

    assert final["state"] == "done", final.get("failure_reason")
    assert refusals["left"] == 0
    assert all(not j.get("hire_tries") for j in await store.jobs(c["id"]))


async def test_the_platform_contractor_is_not_held_to_the_denscore_daily_cap():
    from datetime import datetime, timezone

    from services.net.guards import DAILY_CAPS

    svc = NetworkService(get_firestore_client())
    rate = svc._c("net_rate")
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    await rate.document(workers.CONTRACTOR_ID).set({"day_key": day, "day_count": 250, "minute_key": "",
                                                    "minute_count": 0})
    assert DAILY_CAPS[-1][1] < 250  # an ordinary low-DenScore agent would be refused here
    await svc.check_rate(workers.CONTRACTOR_ID)  # does not raise


class MeteredMeta(FakeMeta):
    """Workers report real token usage, `out_tokens` per answer."""

    def __init__(self, out_tokens, **kw):
        super().__init__(**kw)
        self.out_tokens = out_tokens

    async def create_chat_completion(self, payload, *, bucket=None):
        response = await super().create_chat_completion(payload, bucket=bucket)
        if "response_format" not in payload:
            response["usage"] = {"prompt_tokens": 1_000, "completion_tokens": self.out_tokens}
        return response


async def test_each_answer_is_metered_onto_its_job():
    """The demo shows tokens metered per job; a job's cost is its workers' real Meta usage, summed over reworks."""
    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    meta = MeteredMeta(out_tokens=10_000, fail_first_judgment_of={"Task: Find a replacement and price."})
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test")
    c = await store.create_contract(owner_agent=await _buyer("buyer-meter"), surface="cli", goal="Metered", budget_cents=2_000)
    for _ in range(12):
        await workers.serve_once(svc=svc, meta=meta)
        await engine.tick()
    jobs = {j["capability"]: j for j in await store.jobs(c["id"])}
    # 1,000 in at $1.25/M plus 10,000 out at $4.25/M is 4.375¢, rounded up to 5¢ per answer.
    assert jobs["vision"]["pending_cost_cents"] == 5
    assert jobs["research"]["pending_cost_cents"] == 10  # answered twice: the first try failed review


async def test_a_job_whose_metered_cost_passes_its_budget_stops_unpaid():
    """A job stops at its budget: an answer that cost more than the job pays fails the job instead of being paid."""
    svc, store = NetworkService(get_firestore_client()), _store()
    await _house(svc)
    meta = MeteredMeta(out_tokens=1_000_000)  # $4.25 an answer, over the $3 vision job
    engine = Engine(store=store, svc=svc, meta=meta, owner="engine-test")
    c = await store.create_contract(owner_agent=await _buyer("buyer-over"), surface="cli", goal="Over budget", budget_cents=2_000)
    for _ in range(6):
        await workers.serve_once(svc=svc, meta=meta)
        await engine.tick()
    vision = next(j for j in await store.jobs(c["id"]) if j["capability"] == "vision")
    assert vision["state"] == "failed" and "over the job budget" in vision["failure_reason"]
    assert vision["pending_cost_cents"] == 426 and not vision.get("paid_cents")
