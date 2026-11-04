"""
Den Lens ask (services/contracts/lens_ask.py, POST /lens/ask, /lens/threads, /lens/crew/invite).

Why these matter:
  - most asks need an answer, not a hire: the answer and steps must come back with nothing
    sent, stored or spent;
  - "have my agents do it" goes to the wearer's own crew, never to the house crew (house
    workers are paid per contract, the wearer's agents are not);
  - with no crew, nothing is sent and Spark is told so, so it answers instead of claiming
    the task was handed off;
  - a hire is only an offer: no contract is created and the budget a model hears is clamped
    to the $50 float cap; the crop is stored only for an offer, never for a plain answer;
  - a model that keeps calling tools is cut off, so one ask cannot loop on Meta credit;
  - den_ask (any agent) works without a photo, books its spend to the free community share and
    gets a longer answer; an image URL goes to Meta as is, so we never fetch arbitrary URLs; the
    free tier stops at 20 asks a day per agent on every instance;
  - a crew invite hands back a message any agent can act on, with the code in it, and the
    wearer sees only replies from other agents on the thread.
"""

import io
import json
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from services.contracts import lens_ask


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (640, 480), "gray").save(out, "JPEG")
    return out.getvalue()


def _call(name, args, call_id="c1"):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": call_id}


def _message(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text, "annotations": []}]}


SEARCH = {"type": "web_search_call", "action": {"type": "search", "sources": [
    {"type": "url", "url": "https://www.poolweb.com/impeller"}, {"type": "url", "url": "https://www.poolweb.com/impeller"}]}}


class FakeMeta:
    """Plays back Responses outputs in order; SAM (model sam-3.1) answers with one box."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.payloads = []
        self.buckets = []

    async def create_response(self, payload, bucket=None):
        self.payloads.append(payload)
        self.buckets.append(bucket)
        if payload["model"] == "sam-3.1":
            return {"output": [_message("<|box;x1=10;y1=10;x2=200;y2=200;w=640;h=480|>")]}
        return {"id": f"resp_{len(self.payloads)}", "output": self.outputs.pop(0)}


class FakeNet:
    def __init__(self):
        self.sent = []

    async def send(self, sender, **kw):
        self.sent.append((sender, kw))
        return {"thread_id": "th_1", "recipients": [{"to": "my-openclaw/main"}, {"to": "my-claude/repo"}, {"to": "my-claude/cli"}]}


@pytest.fixture
def crews(monkeypatch):
    found = []

    async def crews_of(svc, agent_id):
        return found

    monkeypatch.setattr("services.net.autonomy.crews_of", crews_of)
    return found


def _no_store(agent_id, jpeg):
    raise AssertionError("nothing may be stored for this ask")


async def test_a_plain_answer_sends_and_stores_nothing(crews):
    meta, net = FakeMeta([SEARCH, _message("**Impeller 073129**, about $98.\n- Shut off power\n- Remove the motor\nnot a step")]), FakeNet()

    out = await lens_ask.ask(meta, net, "wearer", "what part is this?", _jpeg(), store=_no_store)

    assert out["answer"] == "Impeller 073129, about $98."
    assert out["steps"] == ["Shut off power", "Remove the motor"]
    assert out["sources"] == [{"title": "poolweb.com", "url": "https://www.poolweb.com/impeller"}]
    assert out["crew"] is None and out["hire"] is None and net.sent == []
    assert {"type": "web_search"} in meta.payloads[0]["tools"]


async def test_a_task_goes_to_the_wearers_own_crew_not_the_house(crews):
    crews += [{"name": "den-house"}, {"name": "wearer-crew"}]
    meta, net = FakeMeta([_call("ask_my_crew", {"task": "Order impeller 073129"})], [_message("Sent to your agents.")]), FakeNet()

    out = await lens_ask.ask(meta, net, "wearer", "have my agents order it", _jpeg(), store=_no_store)

    sender, kw = net.sent[0]
    assert sender == "wearer" and kw["to"] == "crew:wearer-crew" and kw["type"] == "ask"
    assert kw["body"].startswith("Order impeller 073129")
    assert out["crew"] == {"crew": "wearer-crew", "thread_id": "th_1", "sent_to": ["my-claude", "my-openclaw"]}
    follow_up = meta.payloads[1]
    assert follow_up["previous_response_id"] == "resp_1"
    assert follow_up["input"][0]["call_id"] == "c1" and "2 agents" in follow_up["input"][0]["output"]


async def test_no_crew_means_nothing_sent_and_spark_is_told(crews):
    crews.append({"name": "den-house"})
    meta, net = FakeMeta([_call("ask_my_crew", {"task": "Order it"})], [_message("Add agents in settings.")]), FakeNet()

    out = await lens_ask.ask(meta, net, "wearer", "have my agents order it", _jpeg(), store=_no_store)

    assert net.sent == [] and out["crew"] is None
    assert meta.payloads[1]["input"][0]["output"] == lens_ask.NO_CREW


async def test_a_hire_is_only_an_offer_with_a_capped_budget(crews):
    stored = []
    meta, net = FakeMeta(
        [_call("offer_hire", {"goal": "Find and buy the impeller", "budget_usd": 2000,
                              "acceptance_hints": ["part number", "link"], "target_label": "impeller"})],
        [_message("Confirm the offer on your display.")]), FakeNet()

    out = await lens_ask.ask(meta, net, "wearer", "hire someone, two thousand dollars", _jpeg(),
                             store=lambda a, j: stored.append(j) or "contracts/evidence/wearer/lens-1.jpg")

    hire = out["hire"]
    assert hire["budget_cents"] == 5_000 and hire["goal"] == "Find and buy the impeller"
    assert hire["evidence"] == ["contracts/evidence/wearer/lens-1.jpg"] and len(stored) == 1
    assert hire["target"]["box"] is not None and hire["crop_jpeg_b64"]
    assert net.sent == []


async def test_a_model_that_keeps_calling_tools_is_cut_off(crews):
    loop = [_call("ask_my_crew", {"task": "again"}, call_id=f"c{i}") for i in range(lens_ask.MAX_ROUNDS)]
    meta = FakeMeta(*([c] for c in loop))

    out = await lens_ask.ask(meta, FakeNet(), "wearer", "loop", _jpeg(), store=_no_store)

    assert len(meta.payloads) == lens_ask.MAX_ROUNDS
    assert out["answer"] == "I couldn't find an answer."


async def test_den_ask_for_any_agent_needs_no_photo_and_uses_the_free_share(crews):
    meta = FakeMeta([_message("x" * 1_400)])

    out = await lens_ask.ask(meta, FakeNet(), "some-agent", "best way to descale a kettle?", surface=lens_ask.AGENT,
                             bucket="community", store=_no_store)

    content = meta.payloads[0]["input"][0]["content"]
    assert [c["type"] for c in content] == ["input_text"] and meta.buckets == ["community"]
    assert len(out["answer"]) == 1_400 and "smart glasses" not in meta.payloads[0]["instructions"]


async def test_den_ask_image_url_goes_to_meta_and_a_hire_has_no_crop(crews):
    meta = FakeMeta([_call("offer_hire", {"goal": "Fix it", "budget_usd": 5, "acceptance_hints": [], "target_label": "pump"})],
                    [_message("Offer ready.")])

    out = await lens_ask.ask(meta, FakeNet(), "some-agent", "hire someone", image_url="https://img.example.com/p.jpg",
                             surface=lens_ask.AGENT, store=_no_store)

    assert meta.payloads[0]["input"][0]["content"][1] == {"type": "input_image", "image_url": "https://img.example.com/p.jpg"}
    assert out["hire"]["evidence"] == [] and out["hire"]["crop_jpeg_b64"] is None
    assert all(p["model"] != "sam-3.1" for p in meta.payloads)


emulator = pytest.mark.skipif(not os.environ.get("FIRESTORE_EMULATOR_HOST"), reason="needs the Firestore emulator")


@emulator
async def test_crew_invite_gives_a_pasteable_join_and_threads_show_only_replies():
    from uuid import uuid4

    from routers.contracts import lens_crew_invite, lens_thread
    from services.net import autonomy
    from services.net.service import get_network_service

    wearer = SimpleNamespace(agent_id=f"lens-{uuid4().hex[:8]}")
    first = await lens_crew_invite(wearer)
    again = await lens_crew_invite(wearer)

    assert first["crew"] == again["crew"] == f"{wearer.agent_id}-crew"
    assert first["invite_code"] in first["message"] and "net_crew" in first["message"]
    assert again["invite_code"] != first["invite_code"]  # a fresh code each time; the old one stops working

    helper = f"helper-{uuid4().hex[:8]}"
    svc = get_network_service()
    for a in (wearer.agent_id, helper):  # the network only delivers between registered agents
        await svc._c("agents").document(a).set({"agent_id": a, "status": "active"})
    await autonomy.join(svc, helper, again["crew"], again["invite_code"])
    await svc.register_session(helper, runtime="openclaw", name="main")
    sent = await svc.send(wearer.agent_id, to=f"crew:{again['crew']}", type="ask", body="Order the impeller")
    task_id = sent["id"]  # one live session: the message to it
    await svc.send(helper, to=wearer.agent_id, type="answer", body="Ordered, arrives Friday", reply_to=task_id)

    replies = (await lens_thread(sent["thread_id"], wearer))["replies"]
    assert [(r["from_agent"], r["body"]) for r in replies] == [(helper, "Ordered, arrives Friday")]


@emulator
async def test_free_tier_allows_twenty_asks_a_day_per_agent():
    from uuid import uuid4

    agent = f"free-{uuid4().hex[:8]}"
    for _ in range(lens_ask.FREE_ASKS_PER_DAY):
        await lens_ask.take_free_ask(agent)
    with pytest.raises(lens_ask.FreeTierUsed):
        await lens_ask.take_free_ask(agent)
    await lens_ask.take_free_ask(f"other-{uuid4().hex[:8]}")  # per agent, not global
