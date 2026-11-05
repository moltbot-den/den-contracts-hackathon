"""
Meta Model API client and its spend guard (hackathon PRD 5.1).

What these tests pin down, and why each matters for money:
  - cost is computed in integer micro-USD from `usage`, rounded UP, with cached
    input billed at the cached rate, so recorded spend is never below the bill;
  - a terminal response with no `usage` records a conservative estimate, never
    zero, so an unmetered call cannot slip past the cap;
  - the guard fails closed: at or over the cap, and when the spend total cannot
    be read, no call reaches Meta;
  - `sam-3.1` has its own sub-cap, so Den Lens can never burn the whole budget
    on segmentation;
  - the $100 alert fires exactly once even when concurrent calls cross it;
  - the day doc and the total doc never diverge;
  - a 429 is retried after `Retry-After`, and spend is recorded once per call.

The cap paths run against the Firestore emulator because the guarantees live
in Firestore increments, batches and create-if-absent, which mocks cannot
exercise.
"""

import asyncio
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from services.meta_client import (
    MetaAPIError,
    MetaClient,
    MetaSpendGuard,
    SpendCapExceeded,
    cost_micro,
)

BASE = "https://api.meta.ai/v1"


def _settings(**overrides):
    base = dict(
        meta_api_key="test-key",
        meta_api_base_url=BASE,
        meta_spend_cap_usd=140,
        meta_spend_alert_usd=100,
        meta_sam_cap_usd=5,
        meta_gateway_cap_usd=10,
        firestore_collection_prefix=f"metaspend{uuid.uuid4().hex[:8]}",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ──────────────────────────────────────────────────────────────────────
# Pure pricing math (no Firestore)
# ──────────────────────────────────────────────────────────────────────


def test_cost_bills_cached_input_at_the_cached_rate_and_rounds_up():
    # Spark: $1.25 in, $0.15 cached, $4.25 out per 1M tokens.
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "input_tokens_details": {"cached_tokens": 400_000}}
    # 600k * 1.25 + 400k * 0.15 + 1M * 4.25 = 0.75 + 0.06 + 4.25 = $5.06
    assert cost_micro("muse-spark-1.3", {"usage": usage}) == 5_060_000
    # One output token of Wonder is $0.00002 = 20 micro-USD; a single input token
    # of Spark is 1.25 micro-USD and must round up to 2, never down to 1.
    assert cost_micro("muse-wonder-1.0", {"usage": {"input_tokens": 0, "output_tokens": 1}}) == 20
    assert cost_micro("muse-spark-1.3", {"usage": {"input_tokens": 1, "output_tokens": 0}}) == 2


def test_chat_completions_usage_shape_is_priced_the_same():
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0,
             "prompt_tokens_details": {"cached_tokens": 1_000_000}}
    assert cost_micro("muse-spark-1.3", {"usage": usage}) == 150_000


def test_sam_is_billed_per_image_not_per_token():
    # $2.50 per 1,000 images = 2,500 micro-USD per image.
    assert cost_micro("sam-3.1", {"usage": {"input_tokens": 9_999, "output_tokens": 9_999}}) == 2_500


def test_web_search_queries_are_billed_on_top_of_tokens():
    # $2.50 per 1,000 queries: an ask that searched three times must not be booked as tokens only.
    response = {"usage": {"input_tokens": 0, "output_tokens": 0},
                "output": [{"type": "web_search_call"}] * 3 + [{"type": "message"}]}
    assert cost_micro("muse-spark-1.3", response) == 7_500


def test_missing_usage_is_never_free():
    assert cost_micro("muse-wonder-1.0", {"status": "completed"}, prompt_chars=3_000, max_output_tokens=4_000) > 0
    assert cost_micro("muse-spark-1.3", {}, prompt_chars=0, max_output_tokens=0) > 0


def test_unknown_model_is_refused_rather_than_priced_at_zero():
    with pytest.raises(ValueError):
        cost_micro("muse-mystery-9", {"usage": {"input_tokens": 1, "output_tokens": 1}})


# ──────────────────────────────────────────────────────────────────────
# Fail closed when the total cannot be read (no Firestore needed)
# ──────────────────────────────────────────────────────────────────────


class _BrokenDoc:
    async def get(self):
        raise RuntimeError("firestore unavailable")


def _broken_fs():
    collection = SimpleNamespace(document=lambda _id: _BrokenDoc())
    return SimpleNamespace(db=SimpleNamespace(collection=lambda _name: collection), _collection=lambda n: n)


async def test_guard_fails_closed_when_the_total_cannot_be_read():
    guard = MetaSpendGuard(fs=_broken_fs(), settings=_settings())
    with pytest.raises(SpendCapExceeded):
        await guard.check("muse-spark-1.3")


@respx.mock
async def test_no_request_reaches_meta_when_the_guard_refuses():
    route = respx.post(f"{BASE}/responses").mock(return_value=httpx.Response(200, json={}))
    client = MetaClient(settings=_settings(), guard=MetaSpendGuard(fs=_broken_fs(), settings=_settings()))
    with pytest.raises(SpendCapExceeded):
        await client.create_response({"model": "muse-spark-1.3", "input": "hi"})
    assert not route.called


# ──────────────────────────────────────────────────────────────────────
# Retries (guard stubbed)
# ──────────────────────────────────────────────────────────────────────


def _stub_guard():
    return SimpleNamespace(check=AsyncMock(), record=AsyncMock())


@respx.mock
async def test_429_is_retried_after_retry_after_and_recorded_once():
    done = {"id": "resp_1", "status": "completed", "usage": {"input_tokens": 10, "output_tokens": 10}}
    respx.post(f"{BASE}/responses").mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "5"}, json={"error": {}}),
        httpx.Response(200, json=done),
    ])
    guard = _stub_guard()
    client = MetaClient(settings=_settings(), guard=guard)
    with patch("services.meta_client.asyncio.sleep", new=AsyncMock()) as sleep:
        out = await client.create_response({"model": "muse-spark-1.3", "input": "hi"})
    assert out["id"] == "resp_1"
    sleep.assert_awaited_once_with(5.0)
    guard.record.assert_awaited_once()


@respx.mock
async def test_a_content_filter_400_is_retried_because_the_filter_flaps():
    """Regression: a harmless prompt refused once failed a house-worker job three times (2026-10-31)."""
    filtered = httpx.Response(400, json={"error": {"code": "content_policy_violation"}})
    ok = httpx.Response(200, json={"id": "c", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    respx.post(f"{BASE}/chat/completions").mock(side_effect=[filtered, ok])
    client = MetaClient(settings=_settings(), guard=_stub_guard())
    with patch("services.meta_client.asyncio.sleep", new=AsyncMock()):
        out = await client.create_chat_completion({"model": "muse-spark-1.3", "messages": []})
    assert out["id"] == "c"
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(400, json={"error": {"code": "invalid_request"}}))
    with pytest.raises(MetaAPIError):
        await client.create_chat_completion({"model": "muse-spark-1.3", "messages": []})


@respx.mock
async def test_a_persistently_filtered_prompt_logs_warning_not_error(caplog):
    """The error log must mean 'someone acts': a refused prompt is per-input, a 500 is not."""
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(400, json={"error": {"code": "content_policy_violation"}}))
    client = MetaClient(settings=_settings(), guard=_stub_guard())
    with patch("services.meta_client.asyncio.sleep", new=AsyncMock()), caplog.at_level("WARNING"):
        with pytest.raises(MetaAPIError):
            await client.create_chat_completion({"model": "muse-spark-1.3", "messages": []})
    assert [r.levelname for r in caplog.records if "meta_api.error" in r.message] == ["WARNING"]
    caplog.clear()
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(500, text="boom"))
    with caplog.at_level("WARNING"), pytest.raises(MetaAPIError):
        await client.create_chat_completion({"model": "muse-spark-1.3", "messages": []})
    assert [r.levelname for r in caplog.records if "meta_api.error" in r.message] == ["ERROR"]


@respx.mock
async def test_background_create_reserves_the_worst_case_until_it_finishes():
    # A queued Wonder response has no usage yet. Booking nothing would let many
    # queued plans pass the cap check at once; booking the worst case cannot.
    respx.post(f"{BASE}/responses").mock(return_value=httpx.Response(200, json={"id": "resp_2", "status": "queued"}))
    respx.get(f"{BASE}/responses/resp_2").mock(return_value=httpx.Response(
        200, json={"id": "resp_2", "status": "completed", "usage": {"input_tokens": 5, "output_tokens": 5}}))
    guard = _stub_guard()
    client = MetaClient(settings=_settings(), guard=guard)
    payload = {"model": "muse-wonder-1.0", "input": "x" * 300, "background": True, "max_output_tokens": 4_000}
    await client.create_response(payload)
    # 100 input tokens at $4/M plus 4,000 output tokens at $20/M = 400 + 80,000 micro-USD.
    guard.record.assert_awaited_once_with("muse-wonder-1.0", 80_400, {}, bucket=None,
                                          response_id="resp_2", reserve=True)
    await client.get_response("resp_2", model="muse-wonder-1.0")
    assert guard.record.await_args.kwargs.get("reserve", False) is False
    assert guard.record.await_count == 2


@respx.mock
async def test_background_create_without_an_output_cap_is_refused():
    client = MetaClient(settings=_settings(), guard=_stub_guard())
    with pytest.raises(ValueError):
        await client.create_response({"model": "muse-wonder-1.0", "input": "plan", "background": True})


# ──────────────────────────────────────────────────────────────────────
# Cap, sub-cap, alert and totals against the Firestore emulator
# ──────────────────────────────────────────────────────────────────────

emulator = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="needs the Firestore emulator (set FIRESTORE_EMULATOR_HOST or run scripts/test-api.sh)",
)


@pytest.fixture
def guard():
    from google.cloud.firestore import AsyncClient

    # A fresh collection prefix per test keeps the shared `total` doc isolated.
    settings = _settings()
    prefix = settings.firestore_collection_prefix
    fs = SimpleNamespace(db=AsyncClient(project="moltbot-den"), _collection=lambda n: f"{prefix}_{n}")
    return MetaSpendGuard(fs=fs, settings=settings)


async def _doc(guard, doc_id):
    snap = await guard._ref(doc_id).get()
    return snap.to_dict() if snap.exists else None


@emulator
async def test_guard_refuses_at_the_cap(guard):
    await guard.record("muse-wonder-1.0", 140_000_000, {})
    with pytest.raises(SpendCapExceeded):
        await guard.check("muse-spark-1.3")


@emulator
async def test_guard_allows_below_the_cap(guard):
    await guard.record("muse-wonder-1.0", 139_999_999, {})
    await guard.check("muse-spark-1.3")


@emulator
async def test_sam_sub_cap_stops_sam_but_not_other_models(guard):
    await guard.record("sam-3.1", 5_000_000, {})
    with pytest.raises(SpendCapExceeded):
        await guard.check("sam-3.1")
    await guard.check("muse-spark-1.3")


@emulator
async def test_day_and_total_stay_equal(guard):
    for _ in range(5):
        await guard.record("muse-spark-1.3", 1_234, {"input_tokens": 3, "output_tokens": 4})
    total = await _doc(guard, "total")
    day = await _doc(guard, guard._today())
    assert total["cost_micro"] == day["cost_micro"] == 6_170
    assert total["calls"] == day["calls"] == 5
    assert total["models"]["muse-spark-1.3"]["cost_micro"] == 6_170


@emulator
async def test_alert_fires_exactly_once_under_concurrent_crossing(guard, caplog):
    await guard.record("muse-wonder-1.0", 99_000_000, {})
    with caplog.at_level("ERROR", logger="services.meta_client"):
        await asyncio.gather(*(guard.record("muse-wonder-1.0", 500_000, {}) for _ in range(10)))
    alerts = [r for r in caplog.records if "meta_spend.alert" in r.getMessage()]
    assert len(alerts) == 1
    assert (await _doc(guard, "alert_100"))["total_micro_at_cross"] >= 100_000_000


@emulator
async def test_gateway_share_stops_public_traffic_but_not_contracts(guard):
    # Public LLM gateway traffic must never starve the hackathon contracts of budget.
    await guard.record("muse-spark-1.3", 10_000_000, {}, bucket="gateway")
    with pytest.raises(SpendCapExceeded):
        await guard.check("muse-spark-1.3", bucket="gateway")
    await guard.check("muse-spark-1.3")


@emulator
async def test_free_tier_stops_at_its_daily_share_but_contracts_keep_going(guard):
    # den_ask is free for every agent; a busy day must end the free tier, not the hackathon contracts.
    from services.meta_client import COMMUNITY, COMMUNITY_DAILY_USD

    await guard.record("muse-spark-1.3", COMMUNITY_DAILY_USD * 1_000_000, {}, bucket=COMMUNITY)
    with pytest.raises(SpendCapExceeded, match="today"):
        await guard.check("muse-spark-1.3", bucket=COMMUNITY)
    await guard.check("muse-spark-1.3")


# ──────────────────────────────────────────────────────────────────────
# LLM gateway routes meta/ models to Meta, not the Entity Gateway
# ──────────────────────────────────────────────────────────────────────


@respx.mock
async def test_gateway_routes_meta_models_to_meta_under_the_gateway_share():
    from models.llm import LLMChatCompletionRequest, LLMChatMessage
    from services.llm_proxy_service import LLMProxyService

    gateway = respx.post("https://gateway.agentsecretstore.com/v1/chat/completions").mock(return_value=httpx.Response(200, json={}))
    meta = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "id": "chatcmpl-1", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }))
    guard = _stub_guard()
    proxy = LLMProxyService(SimpleNamespace(
        llm_proxy_timeout_seconds=30.0, llm_proxy_max_tokens_per_request=4096,
        entity_gateway_llm_key="ass_key", entity_gateway_url="https://gateway.agentsecretstore.com",
        agentsecretstore_api_url="https://api.agentsecretstore.com",
    ))
    proxy._meta = MetaClient(settings=_settings(), guard=guard)
    out = await proxy.chat_completion(
        LLMChatCompletionRequest(model="meta/muse-spark-1.3", messages=[LLMChatMessage(role="user", content="hi")]),
    )
    assert out.choices[0].message.content == "ok"
    assert not gateway.called
    assert meta.calls.last.request.read() and b'"model":"muse-spark-1.3"' in meta.calls.last.request.read().replace(b" ", b"")
    guard.check.assert_awaited_once_with("muse-spark-1.3", bucket="gateway")
    assert guard.record.await_args.kwargs["bucket"] == "gateway"


# ──────────────────────────────────────────────────────────────────────
# Review fixes: unpriced models, per-image SAM, transport errors, record once
# ──────────────────────────────────────────────────────────────────────


@respx.mock
async def test_unpriced_model_is_refused_before_any_paid_call():
    route = respx.post(f"{BASE}/responses").mock(return_value=httpx.Response(200, json={}))
    client = MetaClient(settings=_settings(), guard=MetaSpendGuard(fs=_broken_fs(), settings=_settings()))
    with pytest.raises(ValueError):
        await client.create_response({"model": "muse-mystery-9", "input": "hi"})
    assert not route.called


def test_sam_bills_every_image_in_the_request():
    payload = {"input": [{"role": "user", "content": [
        {"type": "input_text", "text": "duck"},
        {"type": "input_image", "image_url": "data:a"}, {"type": "input_image", "image_url": "data:b"},
    ]}]}
    assert cost_micro("sam-3.1", {}, payload=payload) == 5_000


@respx.mock
async def test_timeout_after_sending_records_the_fallback_cost():
    respx.post(f"{BASE}/responses").mock(side_effect=httpx.ReadTimeout("slow"))
    guard = _stub_guard()
    client = MetaClient(settings=_settings(), guard=guard)
    with pytest.raises(Exception) as exc:
        await client.create_response({"model": "muse-spark-1.3", "input": "hi", "max_output_tokens": 500})
    assert getattr(exc.value, "status_code", None) == 503
    assert guard.record.await_args.args[1] > 0  # Meta may have billed it; never book zero


@respx.mock
async def test_unparseable_or_huge_retry_after_is_clamped():
    respx.post(f"{BASE}/responses").mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        httpx.Response(429, headers={"retry-after": "9999"}),
        httpx.Response(200, json={"id": "r", "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
    ])
    client = MetaClient(settings=_settings(), guard=_stub_guard())
    with patch("services.meta_client.asyncio.sleep", new=AsyncMock()) as sleep:
        await client.create_response({"model": "muse-spark-1.3", "input": "hi"})
    assert [c.args[0] for c in sleep.await_args_list] == [2.0, 30.0]


@emulator
async def test_a_finished_response_is_recorded_once_however_often_it_is_read(guard):
    for _ in range(3):
        await guard.record("muse-wonder-1.0", 1_000, {}, response_id="resp_same")
    assert (await _doc(guard, "total"))["calls"] == 1


@emulator
async def test_a_reservation_is_replaced_by_the_actual_cost_once(guard):
    await guard.record("muse-wonder-1.0", 80_000, {}, response_id="resp_bg", reserve=True)
    assert (await _doc(guard, "total"))["cost_micro"] == 80_000  # the guard sees it while queued
    for _ in range(2):
        await guard.record("muse-wonder-1.0", 1_200, {"out_tokens": 60}, response_id="resp_bg")
    total = await _doc(guard, "total")
    assert (total["cost_micro"], total["calls"], total["out_tokens"]) == (1_200, 1, 60)


@emulator
async def test_a_failed_background_run_keeps_its_hold(guard):
    # A failed or cancelled run reports no usage but may have burned tokens;
    # refunding the hold down to the 1,000-token floor would under-count spend.
    await guard.record("muse-wonder-1.0", 80_000, {}, response_id="resp_failed", reserve=True)
    await guard.record("muse-wonder-1.0", 20_000, {"in_tokens": 0, "out_tokens": 0}, response_id="resp_failed")
    assert (await _doc(guard, "total"))["cost_micro"] == 80_000


def test_muse_image_bills_every_generated_image():
    # A diagram request asks for n images; each is $0.01, whatever the usage block says.
    assert cost_micro("muse-image-1.0", {"usage": {"input_tokens": 50}}, payload={"model": "muse-image-1.0", "prompt": "pump", "n": 3}) == 30_000
    assert cost_micro("muse-image-1.0", {}, payload={"model": "muse-image-1.0", "prompt": "pump"}) == 10_000
