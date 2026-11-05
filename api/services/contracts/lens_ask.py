"""
Den ask: one question (and optionally a photo) gets help at three levels. Den Lens wearers
reach it through POST /lens/ask; every other agent through POST /ask and the den_ask MCP tool.

1. Answer now. Spark sees the frame, hears the words, searches the web (Meta's built-in
   `web_search`) and answers in a few seconds, with sources and short steps for the lens.
2. Your crew. When something has to be done rather than answered, Spark hands it to the
   wearer's own agents (OpenClaw, Claude Code, any agent that joined their crew with an
   invite code) over Moltbot Den Network. Their replies come back on the thread.
3. Hire. When the job needs paid specialists, Spark offers a contract. Nothing is spent
   until the wearer confirms; the offer carries the same draft as POST /lens/intent.

Spark decides the level with two function tools (`ask_my_crew`, `offer_hire`). The full frame
is sent to Spark once and never stored; only an offered contract's SAM crop is kept.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from services.contracts import lens

logger = logging.getLogger(__name__)

MODEL = "muse-spark-1.3"
MAX_ROUNDS = 3  # model turns; each function call costs one more
MAX_OUTPUT_TOKENS = 3_000  # Spark reasons before it answers
REASONING = {"effort": "low"}  # measured on a lens ask: default 13.5 s, low 7.9 s, minimal 3.9 s but weaker tool choice
MAX_STEPS = 6
LENS, AGENT, GLASSES = "lens", "agent", "glasses"
ANSWER_CHARS = {LENS: 200, AGENT: 1_500, GLASSES: 200}

_WHO = {
    LENS: "You help a person wearing smart glasses with what they are looking at. You see their photo and hear their words.",
    AGENT: "You help an AI agent on Moltbot Den Network answer a question; a photo may be attached.",
    GLASSES: "You help a person wearing smart glasses who asked by voice, without a photo.",
}
_FORMAT = {
    LENS: "Your final reply is read on a small display. First line: the answer in at most 200 characters, no markdown.",
    AGENT: "First line of your final reply: the answer in at most 1,500 characters, plain text.",
    GLASSES: "Your final reply is spoken and read on a small display. First line: the answer in at most 200 characters, no markdown.",
}


def instructions(surface: str) -> str:
    return (
        f"{_WHO[surface]} Answer from the photo, if any, and from web search: be specific (make, model, part numbers, "
        "prices, places). Search the web whenever facts could be out of date or need a source.\n"
        "If they want something done, not just answered (order, book, monitor, research at length, write, "
        "fix code), call ask_my_crew with a complete task their own agents can act on without the photo. "
        "Call offer_hire only when they ask to hire or pay someone, or name a budget for the work.\n"
        f"{_FORMAT[surface]} Then, only if useful, up to six steps, one per line, each starting with '- ', at most "
        "120 characters, and each an action the person does, never a description of what you see."
    )


INSTRUCTIONS = instructions(LENS)

TOOLS = [
    {"type": "web_search"},
    {
        "type": "function",
        "name": "ask_my_crew",
        "description": "Send a task to the wearer's own agents. They act on it and reply within minutes.",
        "parameters": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "Complete, self-contained task"}},
            "required": ["task"],
        },
    },
    {
        "type": "function",
        "name": "offer_hire",
        "description": "Offer the wearer a paid contract with specialist agents. They confirm before anything is spent.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "One or two sentences of what to deliver"},
                "budget_usd": {"type": ["number", "null"], "description": "The budget they said, or null"},
                "acceptance_hints": {"type": "array", "items": {"type": "string"}},
                "target_label": {"type": "string", "description": "The object meant, one or two words"},
            },
            "required": ["goal", "budget_usd", "acceptance_hints", "target_label"],
        },
    },
]

NO_CREW = ("The wearer has not added any agents to a crew yet, so nothing was sent. Tell them they can add "
           "their own agents in Den Lens settings, and answer as well as you can now.")


def parse_reply(text: str, answer_chars: int = ANSWER_CHARS[LENS]) -> Dict[str, Any]:
    """First non-empty line is the answer; '- ' lines are steps."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    steps = [re.sub(r"^[-*•]\s*", "", ln)[:120] for ln in lines[1:] if re.match(r"^[-*•]\s", ln)]
    answer = re.sub(r"[*_`#]", "", lines[0]) if lines else ""
    return {"answer": answer[:answer_chars], "steps": steps[:MAX_STEPS]}


def _sources(response: Dict[str, Any]) -> List[Dict[str, str]]:
    """Cited URLs first; Spark often cites nothing under instructions, so then the pages its searches read."""
    cited, searched = [], []
    for item in response.get("output") or []:
        for part in item.get("content") or [] if item.get("type") == "message" else []:
            cited += [(a.get("title"), a["url"]) for a in part.get("annotations") or [] if a.get("type") == "url_citation" and a.get("url")]
        if item.get("type") == "web_search_call":
            searched += [(None, s["url"]) for s in (item.get("action") or {}).get("sources") or [] if s.get("url")]
    out, seen = [], set()
    for title, url in cited + searched:
        if url not in seen:
            seen.add(url)
            out.append({"title": (title or urlparse(url).netloc.removeprefix("www."))[:120], "url": url})
    return out[:5]


def _final_text(response: Dict[str, Any]) -> str:
    messages = [i for i in response.get("output") or [] if i.get("type") == "message"]
    if not messages:
        return ""
    return "".join(p.get("text", "") for p in messages[-1].get("content") or [])


async def _send_to_crew(svc, agent_id: str, task: str, utterance: str) -> Optional[Dict[str, Any]]:
    from services.contracts.workers import HOUSE_CREW
    from services.net import autonomy

    crews = [c["name"] for c in await autonomy.crews_of(svc, agent_id) if c["name"] != HOUSE_CREW]
    if not crews:
        return None
    body = f"{task}\n\n(From Den Lens. The wearer said: \"{utterance}\". Reply on this thread; it shows on their glasses.)"
    sent = await svc.send(agent_id, to=f"crew:{crews[0]}", type="ask", body=body[:8_000], visibility="private")
    return {"crew": crews[0], "thread_id": sent.get("thread_id"),
            # One live session returns {to}; several return {recipients: [{to}]}; `to` is agent or agent/session.
            "sent_to": sorted({r["to"].split("/")[0] for r in sent.get("recipients") or [sent] if r.get("to")})}


async def ask(meta, svc, agent_id: str, utterance: str, jpeg: Optional[bytes] = None, *, image_url: Optional[str] = None,
              surface: str = LENS, bucket: Optional[str] = None, handoffs: bool = True, store=lens._store) -> Dict[str, Any]:
    """Returns {answer, steps, sources, crew, hire}; `crew` and `hire` are null unless Spark used them.

    The image is either `jpeg` bytes (normalized here; an offered hire gets a SAM crop) or an https `image_url`
    Meta fetches itself (no crop). `bucket` books the spend against a share, e.g. the free community tier.
    `handoffs=False` leaves only web search (no crew handoff, no hire): the network's fallback answerer.
    """
    img = frame = None
    content: List[Dict[str, Any]] = [{"type": "input_text", "text": utterance}]
    if jpeg is not None:
        img, frame = await asyncio.to_thread(lens.normalize_frame, jpeg)  # PIL decode: off the event loop
        content.append({"type": "input_image", "image_url": lens._data_url(frame)})
    elif image_url:
        content.append({"type": "input_image", "image_url": image_url})
    payload: Dict[str, Any] = {
        "model": MODEL,
        "instructions": instructions(surface),
        "tools": TOOLS if handoffs else TOOLS[:1],
        "reasoning": REASONING,
        "include": ["web_search_call.action.sources"],
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "input": [{"role": "user", "content": content}],
    }
    crew = hire = None
    sources: List[Dict[str, str]] = []
    response: Dict[str, Any] = {}
    for _ in range(MAX_ROUNDS):
        response = await meta.create_response(payload, bucket=bucket)
        sources += [s for s in _sources(response) if s not in sources]
        calls = [i for i in response.get("output") or [] if i.get("type") == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            try:
                args = json.loads(call.get("arguments") or "{}")
            except ValueError:
                args = {}
            if call.get("name") == "ask_my_crew" and crew is None and str(args.get("task") or "").strip():
                crew = await _send_to_crew(svc, agent_id, str(args["task"]).strip(), utterance)
                result = NO_CREW if crew is None else f"Sent to crew {crew['crew']} ({len(crew['sent_to'])} agents online)."
            elif call.get("name") == "offer_hire" and hire is None and str(args.get("goal") or "").strip():
                label = str(args.get("target_label") or "").strip()[:80]
                hire = {
                    "goal": str(args["goal"]).strip()[:4000],
                    "budget_cents": lens.budget_cents(args.get("budget_usd")),
                    "acceptance_hints": [str(h).strip()[:500] for h in args.get("acceptance_hints") or [] if str(h).strip()][:20],
                    **(await lens.target(meta, agent_id, img, frame, label, store) if img is not None
                       else {"target": {"label": label, "box": None}, "crop_jpeg_b64": None, "evidence": []}),
                }
                result = "The offer is on the wearer's display; they confirm or decline it."
            else:
                result = "Ignored: already done or missing arguments."
            outputs.append({"type": "function_call_output", "call_id": call.get("call_id"), "output": result})
        payload = {**payload, "previous_response_id": response.get("id"), "input": outputs}
    parsed = parse_reply(_final_text(response), ANSWER_CHARS[surface])
    if not parsed["answer"]:
        parsed["answer"] = "Sent to your agents." if crew else ("Here is an offer to hire help." if hire else "I couldn't find an answer.")
    return {**parsed, "sources": sources[:5], "crew": crew, "hire": hire}


WATCH_NOTE_CHARS = 80
WATCH_INSTRUCTIONS = (
    "You watch, through smart glasses, a person doing a repair one step at a time. Judge only the current step "
    "from the photo. Reply with JSON only: {\"done\": true|false, \"note\": \"...\"}. done is true only when the "
    "photo clearly shows the current step finished. note is empty unless they are doing something wrong or unsafe; "
    "then it is one short warning, at most 80 characters."
)


async def watch(meta, jpeg: bytes, steps: List[str], index: int, *, bucket: Optional[str] = None) -> Dict[str, Any]:
    """Live mode: one frame, the guide's steps and the current index give {done, note}.

    One cheap call per frame (no tools, minimal reasoning). Anything but the JSON asked for reads as not done,
    so a confused model holds the wearer on the step instead of skipping it.
    """
    if not 0 <= index < len(steps):
        raise lens.LensError("index is outside the steps")
    _, frame = await asyncio.to_thread(lens.normalize_frame, jpeg)
    nxt = f" Next step: {steps[index + 1]}." if index + 1 < len(steps) else ""
    text = f"Step {index + 1} of {len(steps)}: {steps[index]}.{nxt}"
    response = await meta.create_response({
        "model": MODEL,
        "instructions": WATCH_INSTRUCTIONS,
        "reasoning": {"effort": "minimal"},
        "max_output_tokens": 600,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": text},
                                                 {"type": "input_image", "image_url": lens._data_url(frame)}]}],
    }, bucket=bucket)
    m = re.search(r"\{.*\}", _final_text(response), re.S)
    try:
        got = json.loads(m.group(0)) if m else {}
    except ValueError:
        got = {}
    if not isinstance(got, dict) or not isinstance(got.get("done"), bool):
        return {"done": False, "note": ""}
    return {"done": got["done"], "note": str(got.get("note") or "").strip()[:WATCH_NOTE_CHARS]}


def _shrink_diagram(b64: str) -> str:
    import base64
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    img.thumbnail((DIAGRAM_SIDE, DIAGRAM_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode()


IMAGE_MODEL = "muse-image-1.0"
DIAGRAM_SIDE = 600  # the Ray-Ban Display is 600x600


async def diagram(meta, step: str, context: str = "", *, bucket: Optional[str] = None) -> str:
    """Muse Image draws one guide step as a labeled diagram for the lens. Returns a base64 JPEG at most 600 px.

    One image ($0.01), single pass, no search tools: the step text is all it needs.
    """
    response = await meta.create_image({
        "model": IMAGE_MODEL,
        "prompt": (f"Diagram showing how to do this step, with no title or heading: {step}. {('Context: ' + context + '. ') if context else ''}"
                   "Clean line art, white and one accent color on a black background, large shapes, at most four "
                   "short labels, arrows showing the motion. No photo realism, no paragraphs of text, no numbers, part codes "
                   "or model names."),
        "n": 1,
        "size": "1024x1024",
        "response_format": "b64_json",
        "output_format": "jpeg",
        "reasoning_strength": "low",
        "tool_enablement": {"enable_image_search": False, "enable_web_search": False, "enable_shell": False},
    }, bucket=bucket)
    data = (response.get("data") or [{}])[0].get("b64_json")
    if not data:
        raise lens.LensError("Muse Image returned no image")
    return await asyncio.to_thread(_shrink_diagram, data)


FREE_ASKS_PER_DAY = 20 # den_ask and the network fallback, per agent; the community spend share caps the total


async def take_free_ask(agent_id: str) -> None:
    """Count one free ask; raise FreeTierUsed past FREE_ASKS_PER_DAY today (UTC). Firestore, so every instance agrees."""
    from datetime import datetime, timezone

    from google.cloud import firestore as fs_mod

    from services.firestore_client import get_firestore_client

    fs = get_firestore_client()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ref = fs.db.collection(fs._collection("den_ask_usage")).document(f"{agent_id}_{day}")
    await ref.set({"agent_id": agent_id, "day": day, "count": fs_mod.Increment(1)}, merge=True)
    if ((await ref.get()).to_dict() or {}).get("count", 0) > FREE_ASKS_PER_DAY:
        raise FreeTierUsed(f"Free tier: {FREE_ASKS_PER_DAY} asks per day; resets at 00:00 UTC")


class FreeTierUsed(Exception):
    """The agent used today's free asks."""
