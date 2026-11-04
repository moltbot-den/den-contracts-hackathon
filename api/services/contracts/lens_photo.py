"""
Agents ask for eyes: a crew agent asks a Den Lens wearer for a photo, the wearer sees the request on
the glasses and sends one with a pinch, or declines.

Only agents in a crew with the wearer may ask (a stranger cannot turn on someone's camera), and a
wearer has at most MAX_PENDING open requests. The photo lives in the request doc until the
requester fetches it, then the doc is deleted; unanswered requests lapse after TTL.
"""

import base64
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.cloud.firestore_v1.base_query import FieldFilter

from services.contracts import lens

COLLECTION = "lens_photo_requests"
MAX_PENDING = 3
TTL = timedelta(minutes=10)
PHOTO_MAX_SIDE = 768  # small enough for one Firestore doc, big enough to read a label


class PhotoRequestError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _col(fs):
    return fs.db.collection(fs._collection(COLLECTION))


def _live(doc: Dict[str, Any]) -> bool:
    return doc["state"] != "pending" or doc["expires_at"] > _now()


async def request(fs, svc, requester: str, wearer: str, reason: str) -> Dict[str, Any]:
    from services.contracts.workers import HOUSE_CREW
    from services.net import autonomy

    if requester == wearer:
        raise PhotoRequestError(400, "Ask a wearer other than yourself")
    shared = [c for c in await autonomy.crews_of(svc, wearer)
              if c["name"] != HOUSE_CREW and requester in (c.get("members") or [])]
    if not shared:
        raise PhotoRequestError(403, "Only agents in a crew with the wearer may ask for a photo")
    if len(await pending(fs, wearer)) >= MAX_PENDING:
        raise PhotoRequestError(429, f"The wearer already has {MAX_PENDING} open photo requests")
    doc = {"id": f"pr_{uuid.uuid4().hex[:16]}", "wearer": wearer, "requester": requester, "reason": reason.strip()[:200],
           "state": "pending", "created_at": _now(), "expires_at": _now() + TTL}
    await _col(fs).document(doc["id"]).set(doc)
    return {"request_id": doc["id"], "state": "pending", "expires_at": doc["expires_at"]}


async def pending(fs, wearer: str) -> List[Dict[str, Any]]:
    """The wearer's open requests, oldest first. Filtered here so no composite index is needed."""
    docs = [d.to_dict() for d in await _col(fs).where(filter=FieldFilter("wearer", "==", wearer)).limit(50).get()]
    return sorted(({k: d[k] for k in ("id", "requester", "reason", "created_at", "expires_at")}
                   for d in docs if d["state"] == "pending" and _live(d)), key=lambda d: d["created_at"])


async def answer(fs, wearer: str, request_id: str, jpeg: Optional[bytes]) -> Dict[str, Any]:
    """The wearer sends a photo (`jpeg`) or declines (None)."""
    ref = _col(fs).document(request_id)
    snap = await ref.get()
    doc = snap.to_dict() if snap.exists else None
    if not doc or doc["wearer"] != wearer or not _live(doc):
        raise PhotoRequestError(404, "No such photo request")
    if doc["state"] != "pending":
        raise PhotoRequestError(409, "This request was already answered")
    update: Dict[str, Any] = {"state": "declined", "answered_at": _now()}
    if jpeg is not None:
        img, _ = lens.normalize_frame(jpeg)
        img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80)
        update = {**update, "state": "sent", "photo_b64": base64.b64encode(out.getvalue()).decode()}
    await ref.update(update)
    return {"request_id": request_id, "state": update["state"]}


async def fetch(fs, requester: str, request_id: str) -> Dict[str, Any]:
    """The requester's view: {state} while open, the photo once sent (then the request is gone)."""
    ref = _col(fs).document(request_id)
    snap = await ref.get()
    doc = snap.to_dict() if snap.exists else None
    if not doc or doc["requester"] != requester:
        raise PhotoRequestError(404, "No such photo request")
    if not _live(doc):
        await ref.delete()
        return {"request_id": request_id, "state": "expired"}
    if doc["state"] == "pending":
        return {"request_id": request_id, "state": "pending", "expires_at": doc["expires_at"]}
    await ref.delete()
    return {"request_id": request_id, "state": doc["state"], "image_jpeg_b64": doc.get("photo_b64")}
