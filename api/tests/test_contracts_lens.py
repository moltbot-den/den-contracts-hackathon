"""
Den Lens intake (services/contracts/lens.py).

Why these matter:
  - only the SAM crop is ever stored, never the full frame, because the frame can show
    people and places the wearer did not mean to share (PRD 5.6 privacy);
  - the box SAM returns is in its own coordinate frame and must be scaled to the image,
    or the crop shows the wrong thing;
  - a SAM failure must not cost the wearer the draft: they can still hire;
  - the budget the model reads from speech is clamped to what POST /contracts accepts,
    so a misheard "two thousand dollars" cannot pass the $50 float cap.
"""

import base64
import io
import json

import pytest
from PIL import Image, ImageDraw

from services.contracts import lens


def _frame(size=(800, 600), exif=False) -> bytes:
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).ellipse((500, 200, 700, 400), fill="red")
    out = io.BytesIO()
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretPhone"  # Make
        img.save(out, "JPEG", exif=ex)
    else:
        img.save(out, "JPEG")
    return out.getvalue()


class FakeMeta:
    def __init__(self, draft=None, sam_text="<0f>0<|box;x1=250;y1=100;x2=350;y2=200;w=400;h=300|>", sam_error=None):
        self.draft = draft or {"goal": "Identify the red part and find a replacement.", "budget_usd": 20,
                               "acceptance_hints": ["part number", "where to buy"], "target_label": "red ball"}
        self.sam_text, self.sam_error, self.sam_calls = sam_text, sam_error, 0

    async def create_chat_completion(self, payload, *, bucket=None):
        return {"choices": [{"message": {"content": json.dumps(self.draft)}}]}

    async def create_response(self, payload):
        self.sam_calls += 1
        if self.sam_error:
            raise self.sam_error
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": self.sam_text}]}]}


def test_parse_box_scales_from_sam_frame_to_image():
    assert lens.parse_box("<|box;x1=250;y1=100;x2=350;y2=200;w=400;h=300|>", (800, 600)) == (500, 200, 700, 400)
    assert lens.parse_box("no box here", (800, 600)) is None
    assert lens.parse_box("<|box;x1=9;y1=9;x2=1;y2=1;w=10;h=10|>", (10, 10)) is None


def test_sam_prompts_shorten_to_the_head_noun():
    assert lens.sam_prompts("solid red ball") == ["solid red ball", "red ball", "ball"]
    assert lens.sam_prompts("impeller") == ["impeller"]


@pytest.mark.asyncio
async def test_intent_retries_sam_with_a_shorter_label():
    meta = FakeMeta(draft={"goal": "g", "budget_usd": 5, "acceptance_hints": [], "target_label": "solid red ball"})
    texts = iter(["", meta.sam_text])
    real = meta.create_response

    async def first_empty(payload):
        meta.sam_text = next(texts)
        return await real(payload)

    meta.create_response = first_empty
    out = await lens.intent(meta, "agent-1", "x", _frame(), store=lambda a, j: "ref")
    assert meta.sam_calls == 2 and out["target"]["box"] == [500, 200, 700, 400]


def test_budget_is_clamped_to_contract_bounds():
    assert lens.budget_cents(None) == lens.DEFAULT_BUDGET_CENTS
    assert lens.budget_cents(20) == 2_000
    assert lens.budget_cents(2_000) == lens.MAX_BUDGET_CENTS
    assert lens.budget_cents(0.1) == lens.MIN_BUDGET_CENTS


def test_normalize_frame_drops_exif():
    _, out = lens.normalize_frame(_frame(exif=True))
    assert "SecretPhone".encode() not in out
    assert not Image.open(io.BytesIO(out)).getexif()


@pytest.mark.asyncio
async def test_intent_stores_only_the_crop():
    stored = []
    out = await lens.intent(FakeMeta(), "agent-1", "what is this, budget twenty dollars", _frame(),
                            store=lambda agent, jpeg: stored.append(jpeg) or f"contracts/evidence/{agent}/x.jpg")
    assert out["budget_cents"] == 2_000
    assert out["target"] == {"label": "red ball", "box": [500, 200, 700, 400]}
    assert out["evidence"] == ["contracts/evidence/agent-1/x.jpg"]
    assert len(stored) == 1
    crop = Image.open(io.BytesIO(stored[0]))
    assert crop.width < 800 and crop.height < 600  # the crop, not the frame
    assert crop.getpixel((crop.width // 2, crop.height // 2))[0] > 200  # centred on the red object
    assert base64.b64decode(out["crop_jpeg_b64"]) == stored[0]


@pytest.mark.asyncio
async def test_sam_failure_still_returns_the_draft():
    stored = []
    out = await lens.intent(FakeMeta(sam_error=RuntimeError("429")), "agent-1", "fix this", _frame(),
                            store=lambda agent, jpeg: stored.append(jpeg))
    assert out["goal"].startswith("Identify")
    assert out["target"]["box"] is None and out["crop_jpeg_b64"] is None and out["evidence"] == []
    assert stored == []


@pytest.mark.asyncio
async def test_unreadable_image_and_empty_draft_are_refused():
    with pytest.raises(lens.LensError):
        await lens.intent(FakeMeta(), "agent-1", "x", b"not an image" * 20)
    with pytest.raises(lens.LensError):
        await lens.intent(FakeMeta(draft={"goal": " ", "budget_usd": None, "acceptance_hints": [],
                                          "target_label": ""}), "agent-1", "x", _frame())


def test_normalize_frame_rejects_decompression_bomb():
    import io

    import pytest
    from PIL import Image

    from services.contracts import lens

    buf = io.BytesIO()
    Image.new("1", (20_000, 20_000)).save(buf, "PNG")  # a few KB on the wire, 400M pixels decoded
    assert len(buf.getvalue()) < 1_000_000
    with pytest.raises(lens.LensError):
        lens.normalize_frame(buf.getvalue())


def test_a_small_target_keeps_enough_context_to_read_and_a_big_one_is_not_cut():
    img = Image.new("RGB", (1000, 600))
    small = Image.open(io.BytesIO(lens.crop(img, (980, 590, 1000, 600))))
    assert min(small.size) >= 300, "a 20-pixel box still yields a readable crop"
    assert small.size[0] < 1000, "and never the full frame"
    big = Image.open(io.BytesIO(lens.crop(img, (100, 100, 900, 500))))
    assert big.size[0] >= 512 or big.size[1] >= 512
