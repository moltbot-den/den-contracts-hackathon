"""
The marathon contract (hackathon PRD 4, 10): one standing contract that runs until submission.

It walks straight to `running` and stays there. Every `every_minutes` it starts a small public
child contract from GOALS, owned by the marathon's owner, and once that child ends it records
the outcome (state, spend, refund, verifier rejections) in its own event log. Recoveries in the
children show up there too, which is the point: days of unattended work, all visible. Cancelling
the marathon stops it; a child already running finishes on its own.

ponytail: one child at a time; overlap children if the cadence ever needs to beat a child's run time.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

KIND = "marathon"
DEFAULT_EVERY_MINUTES = 240
DEFAULT_CHILD_BUDGET_CENTS = 200

# Small goals across the house workers' capabilities (vision needs a photo, so it is left out).
GOALS = [
    "Find the part number and one seller for a replacement Pentair WhisperFlo pump impeller, and write "
    "a five-step replacement guide.",
    "Explain the three most common causes of a dripping single-handle kitchen faucet and how to fix each.",
    "Write a Python function that validates an IBAN, with three test cases.",
    "Find the recommended tire pressure for a 2019 Toyota Tacoma and write a short how-to for checking it.",
    "Write a one-page checklist for winterizing an outdoor hose bib, with an illustration of the shutoff valve.",
    "Compare two ways to descale an espresso machine and recommend one, with sources.",
    "Write a TypeScript function that parses an ISO 8601 duration such as PT1H30M, with three test cases.",
    "Find the filter model for a Honeywell HPA300 air purifier and write replacement steps.",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def step(engine, doc: Dict[str, Any]) -> None:
    store = engine.store
    if doc["state"] != "running":
        # draft -> planning -> hiring -> running, with no escrow: the children escrow their own budgets.
        nxt = {"draft": "planning", "planning": "hiring", "hiring": "running"}.get(doc["state"])
        if nxt:
            await engine._transition(doc, nxt, payload={"marathon": True})
        return

    child_id = doc.get("current_child")
    if child_id:
        child = await store.get(child_id)
        if child["state"] not in ("done", "failed", "cancelled") or child.get("escrow_state") == "held":
            return  # still running, or its refund has not landed yet
        jobs = await store.jobs(child_id)
        rejections = sum(1 for j in jobs for v in j.get("verdicts") or [] if v.get("verdict") == "fail")
        await engine._event(doc, "marathon.child_done", updates={
            "current_child": None,
            "children_done": doc.get("children_done", 0) + 1,
            "children_failed": doc.get("children_failed", 0) + (child["state"] != "done"),
            "children_spent_cents": doc.get("children_spent_cents", 0) + child.get("spent_cents", 0),
        }, payload={"child": child_id, "state": child["state"], "spent_cents": child.get("spent_cents", 0),
                    "refund_credits_cents": child.get("refund_credits_cents"), "jobs": len(jobs),
                    "verifier_rejections": rejections, "failure_reason": child.get("failure_reason")})
        return

    last = doc.get("last_child_at")
    if last and _now() - last < timedelta(minutes=doc.get("every_minutes", DEFAULT_EVERY_MINUTES)):
        return
    n = doc.get("children_started", 0)
    child = await store.create_contract(
        owner_agent=doc["owner_agent"], surface="web", goal=GOALS[n % len(GOALS)],
        budget_cents=doc.get("child_budget_cents", DEFAULT_CHILD_BUDGET_CENTS), public=True)
    await engine._event(doc, "marathon.child_started", updates={
        "current_child": child["id"], "children_started": n + 1, "last_child_at": _now(),
    }, payload={"child": child["id"], "goal": child["goal"], "budget_cents": child["budget_cents"]})
