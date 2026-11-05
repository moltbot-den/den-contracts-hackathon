"""den-vision works from the wearer's crop; other house workers and photo-less contracts stay text only."""

import asyncio
import base64

from services.contracts import workers

JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2Q==")


class Doc:
    def __init__(self, store, key):
        self.store, self.key = store, key

    async def get(self):
        return self

    def to_dict(self):
        return self.store.get(self.key)

    async def set(self, value):
        self.store[self.key] = value


class Coll:
    def __init__(self, store):
        self.store = store

    def document(self, key):
        return Doc(self.store, key)


class Svc:
    def __init__(self, evidence):
        self.contracts = {"ct_abc123": {"evidence": evidence}}
        self.sent = []
        self.body = "Contract ct_abc123, job 1-01, attempt 1 (vision).\n\nIdentify the valve."

    def _c(self, name):
        return Coll(self.contracts if name == "contracts" else {})

    async def thread(self, agent_id, thread_id):
        return {"messages": [{"id": "m1", "from_agent": workers.CONTRACTOR_ID, "type": "task", "body": self.body}]}

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs)


class Meta:
    def __init__(self):
        self.payload = None

    async def create_chat_completion(self, payload):
        self.payload = payload
        return {"choices": [{"message": {"content": "It is a brass drain valve."}}]}


def run(agent, svc, monkeypatch, read=lambda ref: JPEG):
    monkeypatch.setattr(workers, "_read_evidence", read)
    meta = Meta()
    msg = {"id": "m1", "thread_id": "t1"}
    assert asyncio.run(workers._work(svc, meta, agent, "persona", msg))
    assert svc.sent, "the worker still answers"
    return meta.payload["messages"]


def test_vision_worker_gets_the_crop(monkeypatch):
    task = run("den-vision", Svc(["contracts/evidence/a/x.jpg"]), monkeypatch)[1]
    assert task["content"][0]["text"].startswith("Contract ct_abc123")
    assert task["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_other_workers_stay_text_only(monkeypatch):
    assert isinstance(run("den-research", Svc(["contracts/evidence/a/x.jpg"]), monkeypatch)[1]["content"], str)


def test_no_evidence_stays_text_only(monkeypatch):
    assert isinstance(run("den-vision", Svc([]), monkeypatch)[1]["content"], str)


def test_a_failed_read_still_gets_the_job_done(monkeypatch):
    def boom(ref):
        raise OSError("bucket down")

    assert isinstance(run("den-vision", Svc(["contracts/evidence/a/x.jpg"]), monkeypatch, read=boom)[1]["content"], str)
