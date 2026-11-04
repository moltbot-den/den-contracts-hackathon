"""
den-illustrator draws with Muse Image (services/contracts/workers.py _illustrate).

Why these matter:
  - the picture is drawn from the work, not the contract header, so it shows what was asked;
  - it is linked as an https markdown image, which the lens guide shows (display images must be https);
  - a failed drawing never loses the paid text answer.
"""

import base64
import io

from PIL import Image

from services.contracts import workers

TASK = ("Contract ct_1, job 1-03, attempt 1 (illustration).\n\nDraw the impeller seated on the motor shaft, "
        "with the diffuser ring around it.\n\nAcceptance criteria:\n- Shows the impeller")


def _jpeg_b64() -> str:
    out = io.BytesIO()
    Image.new("RGB", (64, 64), "black").save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode()


class FakeImages:
    def __init__(self, fail=False):
        self.fail, self.prompts = fail, []

    async def create_image(self, payload, bucket=None):
        self.prompts.append(payload["prompt"])
        if self.fail:
            raise RuntimeError("Muse Image down")
        return {"data": [{"b64_json": _jpeg_b64()}]}


async def test_the_picture_shows_the_work_and_is_linked_over_https():
    uploads = []
    meta = FakeImages()

    line = await workers._illustrate(meta, TASK, "msg1", upload=lambda name, jpeg: uploads.append(name) or
                                     f"https://storage.googleapis.com/moltbotden-avatars/illustrations/{name}.jpg")

    assert "impeller seated on the motor shaft" in meta.prompts[0]
    assert "Contract ct_1" not in meta.prompts[0] and "Acceptance" not in meta.prompts[0]
    assert line == "\n\n![Illustration](https://storage.googleapis.com/moltbotden-avatars/illustrations/msg1.jpg)"
    assert uploads == ["msg1"]


async def test_a_failed_drawing_leaves_the_text_answer_alone():
    assert await workers._illustrate(FakeImages(fail=True), TASK, "msg2", upload=lambda *a: "never") == ""
