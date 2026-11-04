"""
Agents ask for eyes (services/contracts/lens_photo.py, /lens/photo-requests, MCP lens_request_photo).

Why these matter:
  - only a crewmate may ask: a stranger must never be able to put a camera request on someone's glasses;
  - the wearer decides: nothing is sent without their answer, and a decline hands over no photo;
  - the photo is handed over once and then deleted, so photos do not pile up in Firestore;
  - a wearer cannot be flooded: at most MAX_PENDING open requests;
  - over MCP the photo comes back as image content the calling model can look at.
"""

import base64
import io
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from services.contracts import lens_photo

pytestmark = pytest.mark.skipif(not os.environ.get("FIRESTORE_EMULATOR_HOST"), reason="needs the Firestore emulator")


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (2000, 1500), "red").save(out, "JPEG")
    return out.getvalue()


async def _crew():
    from services.firestore_client import get_firestore_client
    from services.net import autonomy
    from services.net.service import get_network_service

    svc = get_network_service()
    wearer, helper, stranger = (f"{p}-{uuid4().hex[:8]}" for p in ("wearer", "helper", "stranger"))
    for a in (wearer, helper, stranger):
        await svc._c("agents").document(a).set({"agent_id": a, "status": "active"})
    crew = await autonomy.create_crew(svc, wearer, f"{wearer}-crew")
    inv = await autonomy.invite(svc, wearer, crew["name"])
    await autonomy.join(svc, helper, crew["name"], inv["invite_code"])
    return get_firestore_client(), svc, wearer, helper, stranger


async def test_only_a_crewmate_may_ask_and_the_photo_is_handed_over_once():
    fs, svc, wearer, helper, stranger = await _crew()

    with pytest.raises(lens_photo.PhotoRequestError) as e:
        await lens_photo.request(fs, svc, stranger, wearer, "show me your desk")
    assert e.value.status == 403

    req = await lens_photo.request(fs, svc, helper, wearer, "Which pump model is it?")
    [shown] = await lens_photo.pending(fs, wearer)
    assert shown["id"] == req["request_id"] and shown["requester"] == helper and shown["reason"] == "Which pump model is it?"
    assert (await lens_photo.fetch(fs, helper, req["request_id"]))["state"] == "pending"
    with pytest.raises(lens_photo.PhotoRequestError):  # the wearer's photo goes only to the one who asked
        await lens_photo.fetch(fs, stranger, req["request_id"])

    await lens_photo.answer(fs, wearer, req["request_id"], _jpeg())
    assert await lens_photo.pending(fs, wearer) == []
    got = await lens_photo.fetch(fs, helper, req["request_id"])
    assert got["state"] == "sent"
    assert max(Image.open(io.BytesIO(base64.b64decode(got["image_jpeg_b64"]))).size) <= lens_photo.PHOTO_MAX_SIDE
    with pytest.raises(lens_photo.PhotoRequestError):
        await lens_photo.fetch(fs, helper, req["request_id"])  # gone after the hand-over


async def test_a_decline_hands_over_nothing_and_requests_are_capped():
    fs, svc, wearer, helper, _ = await _crew()
    ids = [(await lens_photo.request(fs, svc, helper, wearer, f"photo {i}"))["request_id"] for i in range(lens_photo.MAX_PENDING)]
    with pytest.raises(lens_photo.PhotoRequestError) as e:
        await lens_photo.request(fs, svc, helper, wearer, "one more")
    assert e.value.status == 429

    await lens_photo.answer(fs, wearer, ids[0], None)
    got = await lens_photo.fetch(fs, helper, ids[0])
    assert got["state"] == "declined" and got.get("image_jpeg_b64") is None
    await lens_photo.answer(fs, wearer, ids[1], None)
    with pytest.raises(lens_photo.PhotoRequestError) as e:
        await lens_photo.answer(fs, wearer, ids[1], _jpeg())  # no photo after a decline
    assert e.value.status == 409


async def test_mcp_returns_the_photo_as_image_content():
    from services.mcp.tools import execute_tool

    fs, svc, wearer, helper, _ = await _crew()
    session = SimpleNamespace(agent_id=helper)
    asked = await execute_tool("lens_request_photo", {"wearer": wearer, "reason": "Label please"}, session, None)
    assert asked["isError"] is False, asked
    request_id = __import__("json").loads(asked["content"][0]["text"])["request_id"]
    await lens_photo.answer(fs, wearer, request_id, _jpeg())

    got = await execute_tool("lens_request_photo", {"request_id": request_id}, session, None)

    assert got["isError"] is False
    assert [c["type"] for c in got["content"]][:2] == ["text", "image"]
    assert got["content"][1]["mimeType"] == "image/jpeg"
