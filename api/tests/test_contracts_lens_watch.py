"""
Den Lens live mode and guide diagrams (services/contracts/lens_ask.py watch and diagram, POST /lens/watch, /lens/diagram).

Why these matter:
  - the lens only advances when Spark says the step is done; a reply that is not the JSON we
    asked for must read as "not done", never as a skipped step;
  - Spark judges the current step, so it must be told which one and see the step after it;
  - each frame is one cheap call: no tools, minimal reasoning, a small token cap, so a ten
    minute session cannot run up Meta credit;
  - a note warns the wearer and fits the display.
"""

import io

import pytest
from PIL import Image

from services.contracts import lens_ask


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (640, 480), "gray").save(out, "JPEG")
    return out.getvalue()


class FakeMeta:
    def __init__(self, text):
        self.text, self.payloads, self.buckets = text, [], []

    async def create_response(self, payload, bucket=None):
        self.payloads.append(payload)
        self.buckets.append(bucket)
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": self.text}]}]}


STEPS = ["Shut off power at the breaker", "Remove the four pump lid bolts", "Lift out the impeller"]


async def test_done_step_advances_with_a_short_note():
    meta = FakeMeta('Looks good.\n{"done": true, "note": "' + "x" * 300 + '"}')

    out = await lens_ask.watch(meta, _jpeg(), STEPS, 1)

    assert out["done"] is True and len(out["note"]) == lens_ask.WATCH_NOTE_CHARS
    text = meta.payloads[0]["input"][0]["content"][0]["text"]
    assert "Step 2 of 3: Remove the four pump lid bolts" in text and "Lift out the impeller" in text


@pytest.mark.parametrize("reply", ["", "Yes it is done", '{"done": "yes"}', "{broken"])
async def test_anything_but_the_json_means_not_done(reply):
    out = await lens_ask.watch(FakeMeta(reply), _jpeg(), STEPS, 0)

    assert out == {"done": False, "note": ""}


async def test_each_frame_is_one_cheap_call():
    meta = FakeMeta('{"done": false, "note": "Breaker still on"}')

    out = await lens_ask.watch(meta, _jpeg(), STEPS, 0, bucket="community")

    p = meta.payloads[0]
    assert out == {"done": False, "note": "Breaker still on"}
    assert "tools" not in p and p["reasoning"] == {"effort": "minimal"} and p["max_output_tokens"] <= 1_000
    assert p["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
    assert meta.buckets == ["community"]


async def test_index_out_of_range_is_refused():
    with pytest.raises(lens_ask.lens.LensError):
        await lens_ask.watch(FakeMeta(""), _jpeg(), STEPS, 3)


def test_live_frames_have_their_own_hourly_cap_apart_from_asks(monkeypatch):
    """A 10-minute session sends ~120 frames; it must neither use up the wearer's 30 asks nor run unbounded."""
    from fastapi import HTTPException

    from routers import contracts

    monkeypatch.setattr(contracts, "_lens_calls", {})
    for _ in range(contracts.WATCH_PER_HOUR):
        contracts._lens_quota("wearer", contracts.WATCH_PER_HOUR, "live frames")
    contracts._lens_quota("wearer")  # asks still open
    with pytest.raises(HTTPException) as e:
        contracts._lens_quota("wearer", contracts.WATCH_PER_HOUR, "live frames")
    assert e.value.status_code == 429


class FakeImages:
    def __init__(self, data):
        self.data, self.payloads = data, []

    async def create_image(self, payload, bucket=None):
        self.payloads.append(payload)
        return {"data": self.data}


async def test_a_diagram_is_one_cheap_image_sized_for_the_lens():
    """Muse Image is $0.01 an image: exactly one, single pass, no search; the lens shows at most 600x600."""
    import base64

    out = io.BytesIO()
    Image.new("RGB", (1024, 1024), "black").save(out, "JPEG")
    meta = FakeImages([{"b64_json": base64.b64encode(out.getvalue()).decode()}])

    b64 = await lens_ask.diagram(meta, "Remove the four pump lid bolts", "Pentair pump impeller swap")

    p = meta.payloads[0]
    assert p["n"] == 1 and p["reasoning_strength"] == "low" and not any(p["tool_enablement"].values())
    assert "four pump lid bolts" in p["prompt"]
    assert max(Image.open(io.BytesIO(base64.b64decode(b64))).size) <= lens_ask.DIAGRAM_SIDE


async def test_no_image_back_is_an_error_not_a_blank_card():
    with pytest.raises(lens_ask.lens.LensError):
        await lens_ask.diagram(FakeImages([]), "step")
