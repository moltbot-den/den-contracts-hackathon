"""
Den Lens intake (hackathon PRD 5.6): one frame and the wearer's words become a contract draft.

Spark reads the utterance and the frame and drafts the goal, budget and acceptance hints, and
names the object the wearer means. SAM 3.1 boxes that object, and only the crop is kept, as
contract evidence in a private bucket. The full frame is never stored. If SAM fails, the draft
still comes back without a crop: the wearer can hire either way.
"""

import asyncio
import base64
import io
import json
import logging
import re
import uuid
from typing import Any, Dict, Optional, Tuple

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

DRAFT_MODEL = "muse-spark-1.3"
SAM_MODEL = "sam-3.1"
FRAME_MAX_SIDE = 1024
MAX_PIXELS = 25_000_000  # 5000x5000; decoded RGB is 3 bytes a pixel
CROP_MAX_SIDE = 600  # the glasses display takes images under 600x600
CROP_MARGIN = 0.08
CROP_MIN_FRACTION = 0.4  # of the frame\'s longest side; still far from the full frame
DEFAULT_BUDGET_CENTS = 1_000
MIN_BUDGET_CENTS, MAX_BUDGET_CENTS = 100, 5_000  # same bounds as POST /contracts
EVIDENCE_BUCKET = "moltbotden-contracts-evidence"

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "budget_usd": {"type": ["number", "null"]},
        "acceptance_hints": {"type": "array", "items": {"type": "string"}},
        "target_label": {"type": "string"},
    },
    "required": ["goal", "budget_usd", "acceptance_hints", "target_label"],
    "additionalProperties": False,
}

DRAFT_INSTRUCTIONS = (
    "A person wearing smart glasses asked for help with what they are looking at. From their words and the "
    "photo, write a contract for a team of agents: `goal` is one or two plain sentences of what to deliver, "
    "naming the object as specifically as the photo allows (make, model, part, visible markings). "
    "`budget_usd` is the budget they said, or null if they said none; it pays the agents, so never put it in "
    "the goal or the hints as a price limit. `acceptance_hints` are two to four "
    "checkable things a good result must contain. `target_label` names the one object they mean in one or two "
    "words, a noun with at most one adjective (\"impeller\", \"red ball\"), for an image segmentation model."
)

BOX_RE = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>")


class LensError(Exception):
    """The request cannot become a draft (bad image, or the drafting model failed)."""


def normalize_frame(jpeg: bytes) -> Tuple[Image.Image, bytes]:
    """Decode, cap the longest side and re-encode, which also drops EXIF (location, device)."""
    try:
        img = Image.open(io.BytesIO(jpeg))
        if img.width * img.height > MAX_PIXELS:  # a tiny PNG can declare gigapixels; reject before decoding
            raise LensError("image is too large")
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise LensError("image is not a readable JPEG or PNG") from e
    img = img.convert("RGB")
    img.thumbnail((FRAME_MAX_SIDE, FRAME_MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return img, out.getvalue()


def parse_box(sam_text: str, size: Tuple[int, int]) -> Optional[Tuple[int, int, int, int]]:
    """The first SAM box, scaled from SAM's coordinate frame to the image's."""
    m = BOX_RE.search(sam_text or "")
    if not m:
        return None
    x1, y1, x2, y2, w, h = map(int, m.groups())
    if not (w and h and x2 > x1 and y2 > y1):
        return None
    sx, sy = size[0] / w, size[1] / h
    return round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy)


def sam_prompts(label: str) -> list:
    """SAM matches short noun phrases: "solid red ball" finds nothing where "red ball" finds the ball."""
    words = label.split()
    return list(dict.fromkeys(" ".join(words[-n:]) for n in (len(words), 2, 1) if 0 < n <= len(words)))


def crop(img: Image.Image, box: Tuple[int, int, int, int]) -> bytes:
    x1, y1, x2, y2 = box
    # A small target needs its surroundings: a worker or a wearer cannot read a 100-pixel crop.
    floor = max(img.size) * CROP_MIN_FRACTION
    w = min(img.width, max((x2 - x1) * (1 + 2 * CROP_MARGIN), floor))
    h = min(img.height, max((y2 - y1) * (1 + 2 * CROP_MARGIN), floor))
    left = min(max(0, (x1 + x2 - w) / 2), img.width - w)
    top = min(max(0, (y1 + y2 - h) / 2), img.height - h)
    piece = img.crop((int(left), int(top), int(left + w), int(top + h)))
    piece.thumbnail((CROP_MAX_SIDE, CROP_MAX_SIDE))
    out = io.BytesIO()
    piece.save(out, "JPEG", quality=85)
    return out.getvalue()


def budget_cents(budget_usd: Any) -> int:
    if not isinstance(budget_usd, (int, float)) or budget_usd <= 0:
        return DEFAULT_BUDGET_CENTS
    return max(MIN_BUDGET_CENTS, min(MAX_BUDGET_CENTS, round(budget_usd * 100)))


def _data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


async def _draft(meta, utterance: str, frame: bytes) -> Dict[str, Any]:
    response = await meta.create_chat_completion({
        "model": DRAFT_MODEL,
        "max_tokens": 3_000,  # Spark reasons before it answers; 300 tokens left no answer at all
        "messages": [
            {"role": "system", "content": DRAFT_INSTRUCTIONS},
            {"role": "user", "content": [{"type": "text", "text": f"They said: {utterance}"},
                                         {"type": "image_url", "image_url": {"url": _data_url(frame)}}]},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "LensDraft", "schema": DRAFT_SCHEMA}},
    })
    try:
        draft = json.loads(response["choices"][0]["message"]["content"] or "")
        if not str(draft.get("goal") or "").strip():
            raise ValueError("empty goal")
        return draft
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise LensError("the drafting model returned no usable draft") from e


async def _segment(meta, label: str, frame: bytes) -> str:
    response = await meta.create_response({
        "model": SAM_MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": label},
            {"type": "input_image", "image_url": _data_url(frame)},
        ]}],
    })
    return "".join(part.get("text", "") for item in response.get("output") or [] if isinstance(item, dict)
                   for part in item.get("content") or [] if isinstance(part, dict))


def _store(agent_id: str, jpeg: bytes) -> str:
    from google.cloud import storage

    ref = f"contracts/evidence/{agent_id}/lens-{uuid.uuid4().hex}.jpg"
    storage.Client().bucket(EVIDENCE_BUCKET).blob(ref).upload_from_string(jpeg, content_type="image/jpeg")
    return ref


async def target(meta, agent_id: str, img: Image.Image, frame: bytes, label: str, store=_store) -> Dict[str, Any]:
    """SAM boxes `label` in the frame; the crop (only the crop) is stored as evidence. Optional: {} parts on failure."""
    box = crop_jpeg = None
    evidence = []
    if label:
        try:
            for prompt in sam_prompts(label):
                box = parse_box(await _segment(meta, prompt, frame), img.size)
                if box:
                    break
        except Exception as e:  # noqa: BLE001 - segmentation is optional; the draft stands without it
            logger.warning("lens.segment_failed agent=%s err=%s", agent_id, e)
        if box:
            crop_jpeg = await asyncio.to_thread(crop, img, box)
            evidence.append(await asyncio.to_thread(store, agent_id, crop_jpeg))
    return {
        "target": {"label": label, "box": list(box) if box else None},
        "crop_jpeg_b64": base64.b64encode(crop_jpeg).decode() if crop_jpeg else None,
        "evidence": evidence,
    }


async def intent(meta, agent_id: str, utterance: str, jpeg: bytes, store=_store) -> Dict[str, Any]:
    img, frame = await asyncio.to_thread(normalize_frame, jpeg)
    draft = await _draft(meta, utterance, frame)
    label = str(draft.get("target_label") or "").strip()[:80]
    return {
        "goal": str(draft["goal"]).strip()[:4000],
        "budget_cents": budget_cents(draft.get("budget_usd")),
        "acceptance_hints": [str(h).strip()[:500] for h in draft.get("acceptance_hints") or [] if str(h).strip()][:20],
        **await target(meta, agent_id, img, frame, label, store),
    }
