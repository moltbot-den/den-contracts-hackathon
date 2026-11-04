"""
The contract judge: Den Verifier first, the Spark judge as fallback (hackathon PRD 5.5, D7).

Why these matter:
  - Den Verifier's verdict is used as is when it answers in time, so the fine-tune is
    really the judge, not a label;
  - a slow (over 60 s) or broken verifier never blocks or fails a job: Spark judges
    instead and the run records why (`fallback_reason`);
  - every verifier call can start a GPU, so a lifetime call budget caps GPU spend, and
    an unset URL means no call at all.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import respx

from services.contracts import judge as judge_mod
from services.firestore_client import get_firestore_client

pytestmark = pytest.mark.emulator

URL = "https://verifier.test/verify"
JOB = {"id": "1-01", "attempt": 1, "instructions": "Name the part.", "acceptance": ["names the part", "gives a price"],
       "deliverable": "It is a WhisperFlo impeller."}


class SparkJudge:
    def __init__(self):
        self.calls = 0

    async def create_chat_completion(self, payload, *, bucket=None):
        self.calls += 1
        verdict = {"criteria": [{"index": 0, "pass": True, "reason": "named"}, {"index": 1, "pass": True, "reason": "ok"}]}
        return {"choices": [{"message": {"content": json.dumps(verdict)}}]}


def _fs():
    real, prefix = get_firestore_client(), f"judge{uuid4().hex[:8]}"
    return SimpleNamespace(db=real.db, _collection=lambda name: f"{prefix}_{name}")


@pytest.fixture
def settings(monkeypatch):
    s = SimpleNamespace(den_verifier_url=URL, den_verifier_token="t", den_verifier_max_calls=30)
    monkeypatch.setattr("config.get_settings", lambda: s)
    return s


async def _calls(fs) -> int:
    snap = await fs.db.collection(fs._collection("verifier_usage")).document("total").get()
    return (snap.to_dict() or {}).get("calls", 0)


@respx.mock
async def test_den_verifier_judges_when_it_answers(settings):
    output = {"verdict": "fail", "per_criterion": [{"criterion": 0, "met": True, "reason": "named"},
                                                   {"criterion": 1, "met": False, "reason": "no price"}]}
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"output": json.dumps(output), "seconds": 3.1}))
    fs, spark = _fs(), SparkJudge()

    run = await judge_mod.judge(fs, spark, "ct_1", JOB)

    assert run["model"] == "den-verifier" and run["fallback_used"] is False and spark.calls == 0
    assert run["verdict"] == "fail" and [c["pass"] for c in run["criteria"]] == [True, False]
    assert json.loads(route.calls[0].request.content)["token"] == "t"
    assert await _calls(fs) == 1


@respx.mock
async def test_a_slow_verifier_falls_back_to_spark_and_says_why(settings):
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("over 60 s"))
    fs, spark = _fs(), SparkJudge()

    run = await judge_mod.judge(fs, spark, "ct_2", JOB)

    assert run["model"] == "fallback" and run["fallback_used"] and spark.calls == 1
    assert "ReadTimeout" in run["fallback_reason"] and run["verdict"] == "pass"


@respx.mock
async def test_the_call_budget_caps_gpu_use(settings):
    settings.den_verifier_max_calls = 1
    route = respx.post(URL).mock(return_value=httpx.Response(500))
    fs, spark = _fs(), SparkJudge()

    await judge_mod.judge(fs, spark, "ct_3", JOB)
    run = await judge_mod.judge(fs, spark, "ct_3", {**JOB, "attempt": 2})

    assert route.call_count == 1 and run["fallback_reason"] == "den-verifier call budget used up"


@respx.mock
async def test_no_url_means_no_verifier_call(settings):
    settings.den_verifier_url = None
    route = respx.post(URL)
    fs = _fs()

    run = await judge_mod.judge(fs, SparkJudge(), "ct_4", JOB)

    assert not route.called and run["model"] == "fallback" and run["fallback_reason"] is None
    assert await _calls(fs) == 0


@respx.mock
async def test_warm_pings_the_verifier_and_never_raises(settings):
    route = respx.post(URL).mock(return_value=httpx.Response(401))
    await judge_mod.warm()
    assert route.call_count == 1 and json.loads(route.calls[0].request.content)["token"] == "warm"
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("cold"))
    await judge_mod.warm()
    settings.den_verifier_url = None
    await judge_mod.warm()
