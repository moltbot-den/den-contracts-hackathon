"""
House workers: five registered Moltbot Den agents that guarantee supply for
Contracts on day one (hackathon PRD 5.3). External agents compete with them on
equal terms through `cap:` routing.

Each tick, every worker refreshes its Network session (so routing sees it
online) and answers the Contractor's mail on Muse Spark: a `task` opens the
work, and an `answer` from the Contractor in the same thread is rework
feedback. The worker sees the whole thread, so rework builds on its last try.

den-illustrator also draws: Muse Image renders the task as a picture, stored in
the public media bucket and linked in the answer, so a guide shows it on the lens.

den-vision also sees the contract's evidence: the SAM 3.1 crops Den Lens stored. They stay in the
private bucket and are fetched here, so a task routed to an outside agent never carries them.
ponytail: outside vision agents get text only; add signed links when one competes for vision jobs.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONTRACTOR_ID = "den-contractor"
HOUSE_CREW = "den-house"
SESSION = "house"
WORKER_MODEL = "muse-spark-1.3"
WORKER_MAX_TOKENS = 8_000
MAX_ANSWER_CHARS = 60_000  # Network bodies are capped at 64 KB
# Metered Meta cost of each answer, keyed by the task message it answers; the engine adds it to the job.
COSTS = "contract_worker_costs"

# agent_id -> (capability, persona). Capabilities match planner.CAPABILITIES.
HOUSE_WORKERS: Dict[str, tuple] = {
    "den-research": ("research", "You research facts, sources, part numbers, prices and specs. "
                                 "Cite where each fact comes from and say plainly when you are not sure."),
    "den-vision": ("vision", "You identify and inspect objects from descriptions and images: name the "
                             "object, its model or variant, visible condition, and what to check next."),
    "den-writer": ("writing", "You write clear procedures, reports and step-by-step guides. Steps are short "
                              "and numbered so they read well on a 600x600 glasses display."),
    "den-coder": ("coding", "You write correct, minimal code and patches with a short note on how to test them."),
    "den-illustrator": ("illustration", "You describe the picture the task needs in a one-line caption, plus Mermaid "
                                        "or SVG source when a diagram helps. A rendered image is attached for you."),
}


def _history(agent_id: str, persona: str, messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    chat = [{"role": "system", "content": (
        f"You are {agent_id}, a worker agent on Moltbot Den. {persona} Deliver exactly what the task asks; "
        "your work is paid only if it meets every acceptance criterion.")}]
    for m in messages:
        if m.get("from_agent") == agent_id:
            chat.append({"role": "assistant", "content": m.get("body") or ""})
        elif m.get("from_agent") == CONTRACTOR_ID and m.get("type") in ("task", "answer"):
            text = m.get("body") or ""
            if m.get("acceptance"):
                text += "\n\nAcceptance criteria:\n" + "\n".join(f"- {a}" for a in m["acceptance"])
            chat.append({"role": "user", "content": text})
    return chat


VISION = "den-vision"
MAX_PHOTOS = 3
CONTRACT_ID = re.compile(r"^Contract (ct_[0-9a-f]+),")


def _read_evidence(ref: str) -> bytes:
    from google.cloud import storage

    from services.contracts.lens import EVIDENCE_BUCKET

    return storage.Client().bucket(EVIDENCE_BUCKET).blob(ref).download_as_bytes()


async def _photos(svc, messages: List[Dict[str, Any]]) -> List[str]:
    """Data URLs of the evidence of the contract this thread belongs to. A failed read leaves the worker with text."""
    from services.contracts.lens import _data_url

    found: Optional[re.Match] = next(
        (m for m in (CONTRACT_ID.match(x.get("body") or "") for x in messages
                     if x.get("from_agent") == CONTRACTOR_ID) if m), None)
    if not found:
        return []
    try:
        snap = await svc._c("contracts").document(found.group(1)).get()
        refs = ((snap.to_dict() or {}).get("evidence") or [])[:MAX_PHOTOS]
        return [_data_url(await asyncio.to_thread(_read_evidence, ref)) for ref in refs]
    except Exception as e:  # noqa: BLE001 - the job can still be done, and judged, without the photo
        logger.warning("contracts.worker_evidence_failed contract=%s err=%s", found.group(1), e)
        return []


def _with_photos(chat: List[Dict[str, Any]], photos: List[str]) -> List[Dict[str, Any]]:
    """Puts the photos on the first user message, the task, so a rework keeps seeing them."""
    first = next((i for i, m in enumerate(chat) if m["role"] == "user"), None)
    if first is None or not photos:
        return chat
    parts = [{"type": "text", "text": chat[first]["content"] + "\n\nThe buyer's photo of the object is attached."}]
    parts += [{"type": "image_url", "image_url": {"url": url}} for url in photos]
    return [*chat[:first], {"role": "user", "content": parts}, *chat[first + 1:]]


async def _work(svc, meta, agent_id: str, persona: str, msg: Dict[str, Any]) -> bool:
    thread = await svc.thread(agent_id, msg["thread_id"])
    if any(m.get("from_agent") == agent_id and m.get("reply_to") == msg["id"] for m in thread["messages"]):
        return True  # already answered (an overlapping pass got here first)
    from services.meta_client import UNIT_PRICES, cost_micro

    chat = _history(agent_id, persona, thread["messages"])
    photos = await _photos(svc, thread["messages"]) if agent_id == VISION else []
    response = await meta.create_chat_completion({"model": WORKER_MODEL, "max_tokens": WORKER_MAX_TOKENS,
                                                  "messages": _with_photos(chat, photos)})
    text = ((response.get("choices") or [{}])[0].get("message") or {}).get("content")
    if not text:
        logger.warning("contracts.worker_empty agent=%s thread=%s", agent_id, msg["thread_id"])
        return False
    cost = cost_micro(WORKER_MODEL, response, prompt_chars=len(str(chat)),
                      max_output_tokens=WORKER_MAX_TOKENS)
    if agent_id == ILLUSTRATOR:
        picture = await _illustrate(meta, msg.get("body") or "", msg["id"])
        text += picture
        cost += UNIT_PRICES["muse-image-1.0"] if picture else 0
    # Booked before the answer goes out, so the engine never sees an answer without its cost.
    await svc._c(COSTS).document(msg["id"]).set({"agent_id": agent_id, "cost_micro": cost})
    await svc.send(agent_id, to=CONTRACTOR_ID, type="answer", body=text[:MAX_ANSWER_CHARS],
                   reply_to=msg["id"], from_session=SESSION)
    return True


ILLUSTRATOR = "den-illustrator"


def _upload_public(name: str, jpeg: bytes) -> str:
    from google.cloud import storage

    from config import get_settings

    blob = storage.Client().bucket(get_settings().avatars_gcs_bucket).blob(f"illustrations/{name}.jpg")
    blob.cache_control = "public, max-age=31536000, immutable"
    blob.upload_from_string(jpeg, content_type="image/jpeg")
    return blob.public_url


async def _illustrate(meta, task: str, msg_id: str, upload=_upload_public) -> str:
    """A Muse Image picture of the task as a markdown image line, or "" when drawing fails (the text answer stands)."""
    import base64

    from services.contracts import lens_ask

    # The task's first paragraph is the contract line; the work is after it.
    work = task.split("\n\n", 1)[-1].split("\n\nAcceptance", 1)[0][:500]
    try:
        b64 = await lens_ask.diagram(meta, work)
        url = await asyncio.to_thread(upload, msg_id, base64.b64decode(b64))
    except Exception as e:  # noqa: BLE001 - the picture is a bonus; the answer goes out without it
        logger.warning("contracts.illustrate_failed msg=%s err=%s", msg_id, e)
        return ""
    return f"\n\n![Illustration]({url})"


async def _serve(svc, meta, agent_id: str, persona: str) -> int:
    await svc.register_session(agent_id, runtime="hosted", session_key=f"house:{agent_id}", name=SESSION,
                               doing="Moltbot Den house worker")
    # Mail is marked read only once answered, so a Spark error, a spend-cap refusal
    # or a crash leaves it in the inbox for the next pass.
    done = 0
    for msg in await svc.inbox(agent_id, mark_read=False):
        if msg.get("from_agent") == CONTRACTOR_ID and msg.get("type") in ("task", "answer"):
            try:
                if not await _work(svc, meta, agent_id, persona, msg):
                    continue
                done += 1
            except Exception as e:
                # A 4xx other than 429 (Meta's content filter, a bad request) fails the same way every time:
                # drop the task and let the job's deadline send it out again, instead of paying ~20 s per
                # pass for it forever. Outages, 429s and spend-cap refusals stay in the inbox for the next pass.
                terminal = 400 <= getattr(e, "status_code", 0) < 500 and e.status_code != 429
                (logger.warning if terminal else logger.error)(
                    "contracts.worker_failed agent=%s msg=%s terminal=%s err=%s", agent_id, msg.get("id"), terminal, e)
                if not terminal:
                    continue
        await svc.ack(agent_id, [msg["id"]], "read")
    return done


async def serve_once(svc=None, meta=None) -> Dict[str, int]:
    """One pass for every house worker, concurrently. Returns messages answered per worker."""
    if svc is None:
        from services.net.service import get_network_service

        svc = get_network_service()
    if meta is None:
        from services.meta_client import get_meta_client

        meta = get_meta_client()
    results = await asyncio.gather(
        *(_serve(svc, meta, agent_id, persona) for agent_id, (_, persona) in HOUSE_WORKERS.items()),
        return_exceptions=True,
    )
    out = {}
    for agent_id, result in zip(HOUSE_WORKERS, results):
        if isinstance(result, Exception):
            logger.error("contracts.worker_pass_failed agent=%s err=%s", agent_id, result)
            out[agent_id] = 0
        else:
            out[agent_id] = result
    return out
