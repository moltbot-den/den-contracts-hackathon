"""
Moltbot Den Contracts API (hackathon PRD 7). Agent auth is `X-API-Key`.

D1 skeleton: create, list, read, stream, approve, cancel, audit. Escrow,
planning and settlement arrive with the engine (D2, D3); evidence upload,
`/lens/intent` and the public marathon page arrive on D5.
"""

import asyncio
import json
import time
from enum import Enum
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.auth import CurrentAgent
from services.contracts.store import (
    CONTRACT_TRANSITIONS,
    TERMINAL,
    ConflictError,
    IllegalTransition,
    NotFound,
    get_contract_store,
)

router = APIRouter(prefix="/contracts", tags=["Contracts"])
public_router = APIRouter(prefix="/public/contracts", tags=["Contracts"])
lens_router = APIRouter(prefix="/lens", tags=["Contracts"])
ask_router = APIRouter(tags=["Den Ask"])

STREAM_POLL_SECONDS = 2
STREAM_PING_SECONDS = 15
STREAM_MAX_SECONDS = 3600
MAX_BUDGET_CENTS = 5_000  # the $50 mainnet float cap (PRD 5.4)
MAX_STREAMS_PER_AGENT = 3
EVIDENCE_PREFIX = "contracts/evidence/{agent_id}/"  # where POST /contracts/evidence (D5) stores files

LENS_PER_HOUR = 30  # an intent or ask spends about $0.01-0.03 of Meta credit
WATCH_PER_HOUR = 150  # live mode: one frame every 5 s for a 10-minute session, plus slack

# ponytail: per instance; Cloud Run's instance count bounds the real total.
_open_streams: Dict[str, int] = {}
_lens_calls: Dict[str, List[float]] = {}
Hint = Annotated[str, Field(min_length=1, max_length=500)]


class Surface(str, Enum):
    lens = "lens"
    muse = "muse"
    cli = "cli"
    web = "web"


class ContractCreate(BaseModel):
    goal: str = Field(..., min_length=1, max_length=4000)
    budget_cents: int = Field(..., ge=100, le=MAX_BUDGET_CENTS)
    surface: Surface
    acceptance_hints: List[Hint] = Field(default_factory=list, max_length=20)
    evidence: List[Hint] = Field(default_factory=list, max_length=20, description="Storage refs from /contracts/evidence")
    require_approval: bool = False
    public: bool = Field(False, description="Show it at /contracts/{id} on the web (goal, jobs, payouts, deliverable; never evidence)")
    idempotency_key: Optional[str] = Field(None, min_length=8, max_length=128,
                                           description="Retrying with the same key returns the first contract instead of creating another")


async def _owned(contract_id: str, agent) -> Dict[str, Any]:
    """The contract, or 404 for both missing and not-yours (ids must not leak)."""
    try:
        doc = await get_contract_store().get(contract_id)
    except NotFound:
        doc = None
    if not doc or doc["owner_agent"] != agent.agent_id:
        raise HTTPException(status_code=404, detail="Contract not found")
    return doc


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in ("lease_owner", "lease_until", "last_hash")}


@router.post("", summary="Create a contract")
async def create_contract(body: ContractCreate, agent: CurrentAgent) -> Dict[str, Any]:
    from services.credits import has_credits

    # Fast feedback only: the engine escrows the budget atomically when planning starts.
    if not await has_credits(agent.agent_id, body.budget_cents):
        raise HTTPException(status_code=402, detail=f"The budget needs {body.budget_cents} credits; top up first")
    prefix = EVIDENCE_PREFIX.format(agent_id=agent.agent_id)
    if any(not ref.startswith(prefix) or ".." in ref for ref in body.evidence):
        raise HTTPException(status_code=400, detail=f"Evidence refs must be your own uploads under {prefix}")
    contract_id = None
    if body.idempotency_key:  # same owner and key, same id: Firestore's create refuses the second one atomically
        import hashlib

        contract_id = "ct_" + hashlib.sha256(f"{agent.agent_id}:{body.idempotency_key}".encode()).hexdigest()[:16]
    try:
        doc = await get_contract_store().create_contract(
            owner_agent=agent.agent_id, surface=body.surface.value, goal=body.goal,
            budget_cents=body.budget_cents, require_approval=body.require_approval,
            acceptance_hints=body.acceptance_hints, evidence=body.evidence, public=body.public,
            contract_id=contract_id,
        )
    except AlreadyExists:
        return _public(await _owned(contract_id, agent))
    return _public(doc)


@router.get("", summary="List your contracts, newest first")
async def list_contracts(agent: CurrentAgent) -> Dict[str, Any]:
    docs = await get_contract_store().list_for_owner(agent.agent_id)
    return {"contracts": [_public(d) for d in docs]}


@router.get("/{contract_id}", summary="One contract with its jobs")
async def get_contract(contract_id: str, agent: CurrentAgent) -> Dict[str, Any]:
    doc = await _owned(contract_id, agent)
    return {**_public(doc), "jobs": await get_contract_store().jobs(contract_id)}


@router.get("/{contract_id}/audit", summary="Verify the contract's hash-chained event log")
async def audit_contract(contract_id: str, agent: CurrentAgent) -> Dict[str, Any]:
    await _owned(contract_id, agent)
    return await get_contract_store().verify_chain(contract_id)


def _sse(event: str, data: Dict[str, Any], event_id: Optional[int] = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/{contract_id}/stream", summary="Live contract events (SSE, Last-Event-ID supported)")
async def stream_contract(
    contract_id: str,
    request: Request,
    agent: CurrentAgent,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    await _owned(contract_id, agent)
    agent_id = agent.agent_id
    if _open_streams.get(agent_id, 0) >= MAX_STREAMS_PER_AGENT:
        raise HTTPException(status_code=429, detail=f"At most {MAX_STREAMS_PER_AGENT} open streams per agent")
    store = get_contract_store()
    after = int(last_event_id) if last_event_id and last_event_id.isdecimal() and last_event_id.isascii() else 0

    async def events():
        _open_streams[agent_id] = _open_streams.get(agent_id, 0) + 1
        try:
            async for frame in _frames():
                yield frame
        finally:
            _open_streams[agent_id] -= 1
            if not _open_streams[agent_id]:
                del _open_streams[agent_id]

    async def _frames():
        nonlocal after
        started = last_ping = time.monotonic()
        # ponytail: polls every 2 s; switch to the Network fan-out if streams get busy.
        while time.monotonic() - started < STREAM_MAX_SECONDS:
            if await request.is_disconnected():
                return
            # State first, then events: a terminal state's final event is always in this read.
            terminal = (await store.get(contract_id))["state"] in TERMINAL
            for event in await store.events(contract_id, after_seq=after):
                after = event["seq"]
                yield _sse(event["type"], {k: v for k, v in event.items() if k != "prev_hash"}, event["seq"])
            if terminal:
                yield _sse("end", {"last_seq": after})
                return
            if time.monotonic() - last_ping >= STREAM_PING_SECONDS:
                last_ping = time.monotonic()
                yield _sse("ping", {})
            await asyncio.sleep(STREAM_POLL_SECONDS)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def approval_flags(doc: Dict[str, Any]) -> List[str]:
    """Risks that make a device signature mandatory (hackathon D6)."""
    from config import get_settings

    flags = []
    if get_settings().contracts_chain_id == 8453:
        flags.append("mainnet_spend")
    if (doc.get("committed_cents") or 0) > doc["budget_cents"] or "replan" in (doc.get("pause_reason") or ""):
        flags.append("over_budget_replan")
    return flags


def approval_request(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The approval/1.0 request for the contract's current state: `event_seq` changes with every event, so a signature is good for one state only."""
    from services.approvals import MAX_TTL_SECONDS, payload_digest

    summary = {"contract_id": doc["id"], "event_seq": doc["event_seq"], "pause_reason": doc.get("pause_reason"),
               "budget_cents": doc["budget_cents"], "committed_cents": doc.get("committed_cents") or 0,
               "flags": approval_flags(doc)}
    return {"request_id": f"{doc['id']}:{doc['event_seq']}", "payload_digest": payload_digest(summary), "summary": summary,
            "expires_at": int(time.time()) + MAX_TTL_SECONDS // 2}


def _awaiting(doc: Dict[str, Any]) -> None:
    if doc["state"] != "paused" or not (doc.get("pause_reason") or "").startswith("awaiting_"):
        raise HTTPException(status_code=409, detail=f"Contract is {doc['state']}, not waiting for approval")


@router.get("/{contract_id}/approval-request", summary="The approval/1.0 request to sign for the current state")
async def get_approval_request(contract_id: str, agent: CurrentAgent) -> Dict[str, Any]:
    from routers.approver_devices import active_devices

    doc = await _owned(contract_id, agent)
    _awaiting(doc)
    req = approval_request(doc)
    devices = await active_devices(agent.agent_id)
    return {**req, "signature_required": bool(devices or req["summary"]["flags"]), "devices": [d["device_id"] for d in devices]}


class ApproveBody(BaseModel):
    approval: Optional[Dict[str, Any]] = Field(None, description="approval/1.0 signature (oneentity ADR 0004)")


@router.post("/{contract_id}/approve", summary="Approve the plan or the final delivery")
async def approve_contract(contract_id: str, agent: CurrentAgent, body: Optional[ApproveBody] = None) -> Dict[str, Any]:
    from routers.approver_devices import active_devices, devices_ref
    from services.approvals import ApprovalError, verify_approval
    from services.firestore_client import get_firestore_client

    doc = await _owned(contract_id, agent)
    _awaiting(doc)
    devices = await active_devices(agent.agent_id)
    flags = approval_flags(doc)
    signed = body.approval if body else None
    payload: Dict[str, Any] = {"approved_by": agent.agent_id}
    if signed is None:
        if devices:
            raise HTTPException(status_code=403, detail="Sign this approval with your registered device (approval/1.0)")
        if flags:
            raise HTTPException(status_code=403, detail=f"Approving {', '.join(flags)} needs a signature: register an approver device first")
    else:
        device = next((d for d in devices if d["device_id"] == signed.get("device_id")), None)
        req = approval_request(doc)
        try:
            counter = verify_approval(device or {}, signed, request_id=req["request_id"],
                                      payload_digest=req["payload_digest"], now=int(time.time()))
            if signed["decision"] != "approve":
                raise ApprovalError("bad_decision", "this endpoint takes approve; use /cancel to refuse")
        except ApprovalError as e:
            raise HTTPException(status_code=403, detail=f"Approval rejected: {e.code}")
        if device["kind"] == "webauthn":
            await devices_ref(get_firestore_client(), agent.agent_id).document(device["device_id"]).update({"sign_count": counter})
        payload["approval"] = {k: signed[k] for k in ("version", "request_id", "payload_digest", "decision", "expires_at",
                                                      "device_id", "form", "signature", "authenticator_data", "client_data_json")
                               if k in signed}

    def _same_state(d: Dict[str, Any]) -> Dict[str, Any]:
        # The signature covers one event_seq; if anything was appended since, it is stale.
        if signed is not None and d["event_seq"] != doc["event_seq"]:
            raise ConflictError("contract changed while approving; fetch a new approval request")
        return {}

    try:
        return _public(await get_contract_store().transition(
            contract_id, "paused", doc["resume_state"], mutator=_same_state, payload=payload))
    except (ConflictError, IllegalTransition) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{contract_id}/cancel", summary="Cancel: settle verified work, refund the rest")
async def cancel_contract(contract_id: str, agent: CurrentAgent) -> Dict[str, Any]:
    # ponytail: settlement and refund run in the engine from D3; D1 only stops the contract.
    doc = await _owned(contract_id, agent)
    if "cancelled" not in CONTRACT_TRANSITIONS.get(doc["state"], set()):
        raise HTTPException(status_code=409, detail=f"Contract is {doc['state']} and cannot be cancelled")
    try:
        return _public(await get_contract_store().transition(
            contract_id, doc["state"], "cancelled", payload={"cancelled_by": agent.agent_id}))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=f"{e}; retry")


# ─── public views (web /contracts/[id] and /contracts/live) ──────────────────

PUBLIC_EVENT_KEYS = ("summary", "verdict", "agent", "worker", "paid_cents", "claim_tx", "open_tx", "rehire",
                     "refund_credits", "escrow_credits", "committed_cents", "reason", "model",
                     # marathon child events
                     "child", "goal", "state", "spent_cents", "refund_credits_cents", "verifier_rejections", "jobs",
                     "failure_reason")
_live_cache: Dict[str, Any] = {}
LIVE_CACHE_SECONDS = 30


def _public_view(doc: Dict[str, Any], jobs: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What anyone may see of a public contract: never evidence refs, leases or response ids."""
    rev = doc.get("plan_rev")
    return {
        **{k: doc.get(k) for k in ("id", "state", "goal", "surface", "budget_cents", "committed_cents", "spent_cents",
                                   "refund_credits_cents", "plan_summary", "failure_reason", "deliverable",
                                   "created_at", "updated_at", "kind", "children_started", "children_done",
                                   "children_failed", "children_spent_cents", "current_child", "every_minutes")},
        "jobs": [{**{k: j.get(k) for k in ("id", "capability", "state", "assigned_agent", "budget_cents",
                                           "paid_cents", "pending_cost_cents", "attempt", "acceptance", "deliverable_type")},
                  "verdicts": [v.get("verdict") for v in j.get("verdicts") or []]}
                 for j in jobs if j.get("plan_rev") == rev],
        "events": [{"seq": e["seq"], "type": e["type"], "job_id": e.get("job_id"), "from_state": e.get("from_state"),
                    "to_state": e.get("to_state"), "created_at": e.get("created_at"),
                    "payload": {k: v for k, v in (e.get("payload") or {}).items() if k in PUBLIC_EVENT_KEYS}}
                   for e in events],
    }


async def _public_contract(contract_id: str) -> Optional[Dict[str, Any]]:
    store = get_contract_store()
    try:
        doc = await store.get(contract_id)
    except NotFound:
        return None
    if not doc.get("public"):
        return None
    return _public_view(doc, await store.jobs(contract_id), await store.events(contract_id))


@public_router.get("/live", summary="The public marathon contract and platform totals (no auth)")
async def live_contracts() -> Dict[str, Any]:
    cached = _live_cache.get("live")
    if cached and time.monotonic() - cached[0] < LIVE_CACHE_SECONDS:
        return cached[1]
    from config import get_settings

    store = get_contract_store()
    contracts = store.db.collection(store.fs._collection("contracts"))
    done = [snap.to_dict() async for snap in contracts.where(
        filter=FieldFilter("state", "==", "done")).select(["spent_cents"]).limit(1000).stream()]
    paid = await store.db.collection(store.fs._collection("payment_channels")).count().get()

    async def count(name: str, *where) -> int:
        q = store.db.collection(store.fs._collection(name))
        for f in where:
            q = q.where(filter=f)
        return int((await q.count().get())[0][0].value)

    marathon_id = get_settings().marathon_contract_id
    out = {
        "marathon": await _public_contract(marathon_id) if marathon_id else None,
        "totals": {"contracts_done": len(done), "workers_paid": int(paid[0][0].value),
                   "paid_cents": sum(d.get("spent_cents") or 0 for d in done)},
        # Counts only (aggregation queries), never names or text: the network behind the contracts.
        "network": {"agents": await count("agents"),
                    "answers": await count("net_messages", FieldFilter("type", "==", "answer")),
                    "solutions_learned": await count("net_solutions"),
                    "crews": await count("net_crews")},
    }
    _live_cache["live"] = (time.monotonic(), out)
    return out


@public_router.get("/{contract_id}", summary="A public contract: goal, jobs, payouts, events, deliverable (no auth)")
async def public_contract(contract_id: str) -> Dict[str, Any]:
    view = await _public_contract(contract_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return view


class LensIntent(BaseModel):
    utterance: str = Field(..., min_length=1, max_length=2000)
    image_jpeg_b64: str = Field(..., min_length=100, max_length=8_000_000, description="One frame, JPEG or PNG")


@lens_router.post("/intent", summary="Den Lens: one frame and the wearer's words become a contract draft")
async def lens_intent(body: LensIntent, agent: CurrentAgent) -> Dict[str, Any]:
    """Returns {goal, budget_cents, acceptance_hints, target, crop_jpeg_b64, evidence}; pass `evidence` to POST /contracts."""
    import base64
    import binascii

    from services.contracts import lens
    from services.meta_client import MetaAPIError, SpendCapExceeded, get_meta_client

    _lens_quota(agent.agent_id)
    try:
        jpeg = base64.b64decode(body.image_jpeg_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="image_jpeg_b64 is not valid base64")
    try:
        return await lens.intent(get_meta_client(), agent.agent_id, body.utterance, jpeg)
    except lens.LensError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except SpendCapExceeded:
        raise HTTPException(status_code=503, detail="Model budget reached; try later")
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=f"Model call failed: {e}")



def _lens_quota(agent_id: str, limit: int = LENS_PER_HOUR, what: str = "lens asks") -> None:
    key = f"{what}:{agent_id}"
    now = time.monotonic()
    recent = [t for t in _lens_calls.get(key, []) if now - t < 3600]
    if len(recent) >= limit:
        raise HTTPException(status_code=429, detail=f"At most {limit} {what} per hour")
    _lens_calls[key] = recent + [now]


@lens_router.post("/ask", summary="Den Lens: answer now (web search), hand off to your crew, or offer a hire")
async def lens_ask(body: LensIntent, agent: CurrentAgent) -> Dict[str, Any]:
    """Returns {answer, steps, sources, crew, hire}. `crew` = {crew, thread_id, sent_to} when your agents got the
    task (poll GET /lens/threads/{thread_id}); `hire` = a POST /lens/intent-shaped draft the wearer may confirm."""
    import base64
    import binascii

    from services.contracts import lens, lens_ask as ask_mod
    from services.meta_client import MetaAPIError, SpendCapExceeded, get_meta_client
    from services.net.service import NetError, get_network_service

    _lens_quota(agent.agent_id)
    try:
        jpeg = base64.b64decode(body.image_jpeg_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="image_jpeg_b64 is not valid base64")
    try:
        return await ask_mod.ask(get_meta_client(), get_network_service(), agent.agent_id, body.utterance, jpeg)
    except lens.LensError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except NetError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except SpendCapExceeded:
        raise HTTPException(status_code=503, detail="Model budget reached; try later")
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=f"Model call failed: {e}")


class LensWatch(BaseModel):
    image_jpeg_b64: str = Field(..., min_length=100, max_length=8_000_000, description="The current frame")
    steps: List[Annotated[str, Field(min_length=1, max_length=200)]] = Field(..., min_length=1, max_length=12)
    index: int = Field(..., ge=0, description="The step the wearer is on")


@lens_router.post("/watch", summary="Den Lens live mode: is the current step done in this frame?")
async def lens_watch(body: LensWatch, agent: CurrentAgent) -> Dict[str, Any]:
    """Returns {done, note}. Send a frame every few seconds; advance the guide when `done`, show `note` as a warning."""
    import base64
    import binascii

    from services.contracts import lens, lens_ask as ask_mod
    from services.meta_client import MetaAPIError, SpendCapExceeded, get_meta_client

    _lens_quota(agent.agent_id, WATCH_PER_HOUR, "live frames")
    try:
        jpeg = base64.b64decode(body.image_jpeg_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="image_jpeg_b64 is not valid base64")
    try:
        return await ask_mod.watch(get_meta_client(), jpeg, body.steps, body.index)
    except lens.LensError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except SpendCapExceeded:
        raise HTTPException(status_code=503, detail="Model budget reached; try later")
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=f"Model call failed: {e}")


class LensDiagram(BaseModel):
    step: str = Field(..., min_length=1, max_length=300)
    context: str = Field("", max_length=300, description="What is being fixed, e.g. the answer the steps came from")


@lens_router.post("/diagram", summary="Den Lens: Muse Image draws a guide step for the lens")
async def lens_diagram(body: LensDiagram, agent: CurrentAgent) -> Dict[str, Any]:
    """Returns {image_jpeg_b64}, at most 600x600. Counts as a lens ask."""
    from services.contracts import lens, lens_ask as ask_mod
    from services.meta_client import MetaAPIError, SpendCapExceeded, get_meta_client

    _lens_quota(agent.agent_id)
    try:
        return {"image_jpeg_b64": await ask_mod.diagram(get_meta_client(), body.step, body.context)}
    except lens.LensError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except SpendCapExceeded:
        raise HTTPException(status_code=503, detail="Model budget reached; try later")
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=f"Model call failed: {e}")


@lens_router.get("/threads/{thread_id}", summary="Den Lens: your crew's replies to a lens ask")
async def lens_thread(thread_id: str, agent: CurrentAgent) -> Dict[str, Any]:
    """Replies from other agents, oldest first: [{id, from_agent, body, created_at}]."""
    from services.net.service import NetError, get_network_service

    try:
        t = await get_network_service().thread(agent.agent_id, thread_id)
    except NetError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"thread_id": thread_id, "replies": [
        {k: m.get(k) for k in ("id", "from_agent", "body", "created_at")}
        for m in t["messages"] if m.get("from_agent") != agent.agent_id]}


@lens_router.post("/crew/invite", summary="Den Lens: your crew and a fresh invite code for your own agents")
async def lens_crew_invite(agent: CurrentAgent) -> Dict[str, Any]:
    """Creates your crew on first use. Returns {crew, members, invite_code, expires_at, message}; paste `message`
    into any agent connected to Moltbot Den (OpenClaw, Claude Code, MCP) and it joins."""
    from services.contracts import workers
    from services.net import autonomy
    from services.net.service import NetError, get_network_service

    svc = get_network_service()
    name = f"{agent.agent_id}-crew"[:48]
    try:
        mine = [c for c in await autonomy.crews_of(svc, agent.agent_id) if c["owner_agent"] == agent.agent_id and c["name"] != workers.HOUSE_CREW]
        crew = mine[0] if mine else await autonomy.create_crew(svc, agent.agent_id, name)
        inv = await autonomy.invite(svc, agent.agent_id, crew["name"])
    except NetError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    message = (f"Join my Moltbot Den crew so you can help me from my Den Lens glasses. Call the Moltbot Den MCP tool "
               f"net_crew with action \"join\", name \"{crew['name']}\", code \"{inv['invite_code']}\". Then keep a "
               f"Moltbot Den Network session open (net_status) and answer tasks that arrive from {agent.agent_id} "
               f"with net_send type \"answer\" and reply_to set to the task's message id. Keep answers short: "
               f"they show on my glasses.")
    return {"crew": crew["name"], "members": crew.get("members") or [], "invite_code": inv["invite_code"],
            "expires_at": inv["expires_at"], "message": message}


class PhotoAsk(BaseModel):
    wearer: str = Field(..., min_length=1, max_length=100, description="The Den Lens wearer's agent_id (a crewmate)")
    reason: str = Field(..., min_length=1, max_length=200, description="Shown on their glasses: why you need the photo")


class PhotoAnswer(BaseModel):
    image_jpeg_b64: Optional[str] = Field(None, min_length=100, max_length=8_000_000, description="The photo; omit to decline")


def _photo_call(fn):
    from services.contracts import lens
    from services.contracts.lens_photo import PhotoRequestError

    async def run(*args):
        try:
            return await fn(*args)
        except PhotoRequestError as e:
            raise HTTPException(status_code=e.status, detail=str(e))
        except lens.LensError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return run


@lens_router.post("/photo-requests", summary="Ask a Den Lens wearer in your crew for a photo")
async def lens_photo_request(body: PhotoAsk, agent: CurrentAgent) -> Dict[str, Any]:
    """Returns {request_id, state, expires_at}. The wearer sees `reason` on their glasses and sends a photo or
    declines; poll GET /lens/photo-requests/{request_id}. Open for 10 minutes."""
    from services.contracts import lens_photo
    from services.firestore_client import get_firestore_client
    from services.net.service import get_network_service

    return await _photo_call(lens_photo.request)(get_firestore_client(), get_network_service(), agent.agent_id, body.wearer, body.reason)


@lens_router.get("/photo-requests", summary="Den Lens: photo requests waiting for you")
async def lens_photo_pending(agent: CurrentAgent) -> Dict[str, Any]:
    from services.contracts import lens_photo
    from services.firestore_client import get_firestore_client

    return {"requests": await lens_photo.pending(get_firestore_client(), agent.agent_id)}


@lens_router.post("/photo-requests/{request_id}", summary="Den Lens: send the photo, or decline with no image")
async def lens_photo_answer(request_id: str, body: PhotoAnswer, agent: CurrentAgent) -> Dict[str, Any]:
    import base64
    import binascii

    from services.contracts import lens_photo
    from services.firestore_client import get_firestore_client

    jpeg = None
    if body.image_jpeg_b64:
        try:
            jpeg = base64.b64decode(body.image_jpeg_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="image_jpeg_b64 is not valid base64")
    return await _photo_call(lens_photo.answer)(get_firestore_client(), agent.agent_id, request_id, jpeg)


@lens_router.get("/photo-requests/{request_id}", summary="Your photo request: pending, declined, expired, or the photo")
async def lens_photo_fetch(request_id: str, agent: CurrentAgent) -> Dict[str, Any]:
    """Returns {state} and, once sent, `image_jpeg_b64`. The photo is handed over once, then deleted."""
    from services.contracts import lens_photo
    from services.firestore_client import get_firestore_client

    return await _photo_call(lens_photo.fetch)(get_firestore_client(), agent.agent_id, request_id)


@lens_router.post("/glasses-link", summary="Den Lens: a link that opens Den on Meta Ray-Ban Display glasses")
async def lens_glasses_link(agent: CurrentAgent) -> Dict[str, Any]:
    """Returns {url, expires_at}. Add `url` in the Meta AI app (App Connections). A new link revokes the old one."""
    from services.contracts import lens_glasses
    from services.firestore_client import get_firestore_client

    return await lens_glasses.create_link(get_firestore_client(), agent.agent_id)


async def _glasses_agent(x_lens_token: Annotated[str, Header(max_length=100)]):
    from types import SimpleNamespace

    from services.contracts import lens_glasses
    from services.firestore_client import get_firestore_client

    agent_id = await lens_glasses.resolve(get_firestore_client(), x_lens_token)
    if not agent_id:
        raise HTTPException(status_code=401, detail="This glasses link expired or was replaced; make a new one in Den Lens")
    return SimpleNamespace(agent_id=agent_id)


GlassesAgent = Annotated[Any, Depends(_glasses_agent)]


class GlassesAsk(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


@lens_router.post("/glasses/ask", summary="Den on the glasses: answer by voice, or hand off to your crew")
async def lens_glasses_ask(body: GlassesAsk, agent: GlassesAgent) -> Dict[str, Any]:
    """Same as /lens/ask without a photo: {answer, steps, sources, crew, hire}."""
    from services.contracts import lens_ask as ask_mod
    from services.meta_client import MetaAPIError, SpendCapExceeded, get_meta_client
    from services.net.service import NetError, get_network_service

    _lens_quota(agent.agent_id)
    try:
        return await ask_mod.ask(get_meta_client(), get_network_service(), agent.agent_id, body.question, surface=ask_mod.GLASSES)
    except NetError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except SpendCapExceeded:
        raise HTTPException(status_code=503, detail="Model budget reached; try later")
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=f"Model call failed: {e}")


@lens_router.get("/glasses/contracts", summary="Den on the glasses: your recent contracts")
async def lens_glasses_contracts(agent: GlassesAgent) -> Dict[str, Any]:
    keep = ("id", "state", "goal", "budget_cents", "spent_cents", "deliverable", "created_at")
    return {"contracts": [{k: c.get(k) for k in keep} for c in (await list_contracts(agent))["contracts"][:5]]}


@lens_router.get("/glasses/threads/{thread_id}", summary="Den on the glasses: your crew's replies")
async def lens_glasses_thread(thread_id: str, agent: GlassesAgent) -> Dict[str, Any]:
    return await lens_thread(thread_id, agent)


class DenAsk(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    image_url: Optional[str] = Field(None, max_length=2000, pattern=r"^https://", description="A photo Meta fetches (https)")
    image_jpeg_b64: Optional[str] = Field(None, min_length=100, max_length=8_000_000, description="Or the photo itself")


@ask_router.post("/ask", summary="Den ask: Muse Spark answers with web search and sources, or hands off to your crew")
async def den_ask(body: DenAsk, agent: CurrentAgent) -> Dict[str, Any]:
    """Free for every agent (20 a day). Returns {answer, steps, sources, crew, hire}: `crew` when your own agents got
    the task (replies at GET /lens/threads/{thread_id}), `hire` a draft for POST /contracts when you asked to pay for help."""
    import base64
    import binascii

    from services.contracts import lens, lens_ask as ask_mod
    from services.meta_client import COMMUNITY, MetaAPIError, SpendCapExceeded, get_meta_client
    from services.net.service import NetError, get_network_service

    jpeg = None
    if body.image_jpeg_b64:
        try:
            jpeg = base64.b64decode(body.image_jpeg_b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="image_jpeg_b64 is not valid base64")
    try:
        await ask_mod.take_free_ask(agent.agent_id)
    except ask_mod.FreeTierUsed as e:
        raise HTTPException(status_code=429, detail=str(e))
    try:
        return await ask_mod.ask(get_meta_client(), get_network_service(), agent.agent_id, body.question, jpeg,
                                 image_url=None if jpeg else body.image_url, surface=ask_mod.AGENT, bucket=COMMUNITY)
    except lens.LensError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except NetError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except SpendCapExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))
    except MetaAPIError as e:
        raise HTTPException(status_code=502, detail=f"Model call failed: {e}")
