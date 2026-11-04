"""
Meta Model API client with a fail-closed spend guard (hackathon PRD 5.1).

Every call to https://api.meta.ai/v1 goes through `MetaClient`, which asks
`MetaSpendGuard` before the request and records the cost after it. Money is
integer micro-USD (1e-6 USD) end to end; no floats.

Guard rules (docs/hackathon/D1-DATA-MODEL.md section 4.2):
  - the cap is cumulative, read from one `meta_spend/total` doc;
  - at or over the cap, or when the total cannot be read, the call is refused;
  - `sam-3.1` has its own sub-cap on top of the total cap;
  - spend is written with atomic increments in one batch to the day doc and
    the total doc, so they never diverge;
  - crossing the alert line fires once, via create-if-absent on `alert_100`.

Calls already in flight when the total crosses the cap still finish and are
recorded; the cap sits below the real budget to absorb that overshoot.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from google.api_core.exceptions import Aborted, AlreadyExists
from google.cloud import firestore as firestore_mod

from config import Settings, get_settings

logger = logging.getLogger(__name__)

MICRO = 1_000_000

# Micro-USD per 1M tokens: (input, cached input, output). Source:
# https://dev.meta.ai/docs/pricing-rate-limits (checked 2026-10-23).
TOKEN_PRICES = {
    "muse-wonder-1.0": (4 * MICRO, 200_000, 20 * MICRO),
    "muse-spark-1.3": (1_250_000, 150_000, 4_250_000),
    "muse-spark-1.3-contributor": (100_000, 2_000, 200_000),
}
# Micro-USD per unit for models not billed per token.
UNIT_PRICES = {
    "sam-3.1": 2_500,  # $2.50 per 1,000 images
    "muse-image-1.0": 10_000,  # $0.01 per image
}
SAM_MODEL = "sam-3.1"
WEB_SEARCH_MICRO = 2_500  # $2.50 per 1,000 web_search queries, on top of tokens
# The free community tier (den_ask for any agent): its own share so it can never starve contracts or the demo.
COMMUNITY = "community"
COMMUNITY_DAILY_USD = 5
COMMUNITY_TOTAL_USD = 30
TERMINAL = {"completed", "incomplete", "failed", "cancelled"}
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT = 30.0


class SpendCapExceeded(Exception):
    """The Meta spend cap is reached, or spend could not be verified."""


class MetaAPIError(Exception):
    def __init__(self, message: str, status_code: int = 502, maybe_billed: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.maybe_billed = maybe_billed


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _image_count(payload: Optional[Dict[str, Any]]) -> int:
    """Images generated (`n`, Muse Image) or images read (input_image parts, SAM)."""
    if (payload or {}).get("prompt") is not None:
        return max(1, int(payload.get("n") or 1))
    parts = [part for msg in (payload or {}).get("input") or [] if isinstance(msg, dict)
             for part in msg.get("content") or [] if isinstance(part, dict)]
    return max(1, sum(1 for part in parts if part.get("type") == "input_image"))


def cost_micro(model: str, response: Dict[str, Any], *, prompt_chars: int = 0, max_output_tokens: int = 0,
               payload: Optional[Dict[str, Any]] = None) -> int:
    """Cost of one response in micro-USD, rounded up. Never zero for a known model."""
    if model in UNIT_PRICES:
        # ponytail: images only; video frames are priced when Den Lens sends video.
        return UNIT_PRICES[model] * _image_count(payload)
    if model not in TOKEN_PRICES:
        raise ValueError(f"no price for Meta model {model!r}")
    p_in, p_cached, p_out = TOKEN_PRICES[model]
    usage = response.get("usage")
    if not usage:
        # No usage: assume ~3 chars per token in, and the full output allowance
        # (at least 1,000 tokens) out, so an unmetered call is never free.
        in_tokens = _ceil_div(prompt_chars, 3)
        out_tokens = max(max_output_tokens, 1_000)
        return _ceil_div(in_tokens * p_in + out_tokens * p_out, MICRO)
    n = _usage_counts(response)
    cached = min(n["cached_tokens"], n["in_tokens"])
    searches = sum(1 for item in response.get("output") or [] if isinstance(item, dict) and item.get("type") == "web_search_call")
    return _ceil_div((n["in_tokens"] - cached) * p_in + cached * p_cached + n["out_tokens"] * p_out, MICRO) + searches * WEB_SEARCH_MICRO


def _usage_counts(response: Dict[str, Any]) -> Dict[str, int]:
    usage = response.get("usage") or {}
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    return {
        "in_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
        "out_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
        "cached_tokens": details.get("cached_tokens", 0),
    }


class MetaSpendGuard:
    def __init__(self, fs=None, settings: Optional[Settings] = None):
        if fs is None:
            from services.firestore_client import get_firestore_client

            fs = get_firestore_client()
        self.fs = fs
        self.settings = settings or get_settings()

    def _ref(self, doc_id: str):
        return self.fs.db.collection(self.fs._collection("meta_spend")).document(doc_id)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async def check(self, model: str, bucket: Optional[str] = None) -> None:
        """Raise SpendCapExceeded unless this model may be called now.

        `bucket="gateway"` marks public LLM gateway traffic, which has its own
        share so it can never starve the hackathon contracts.
        """
        if model not in TOKEN_PRICES and model not in UNIT_PRICES:
            raise ValueError(f"no price for Meta model {model!r}; refusing an unmetered call")
        try:
            snap = await self._ref("total").get()
        except Exception as exc:
            logger.error("meta_spend.read_failed model=%s err=%s", model, exc)
            raise SpendCapExceeded("Meta spend total could not be read; refusing the call") from exc
        total = snap.to_dict() if snap.exists else {}
        if total.get("cost_micro", 0) >= self.settings.meta_spend_cap_usd * MICRO:
            raise SpendCapExceeded(f"Meta spend cap of ${self.settings.meta_spend_cap_usd} reached")
        if model == SAM_MODEL:
            sam = (total.get("models") or {}).get(SAM_MODEL, {}).get("cost_micro", 0)
            if sam >= self.settings.meta_sam_cap_usd * MICRO:
                raise SpendCapExceeded(f"SAM spend cap of ${self.settings.meta_sam_cap_usd} reached")
        if bucket == "gateway":
            used = (total.get("buckets") or {}).get("gateway", {}).get("cost_micro", 0)
            if used >= self.settings.meta_gateway_cap_usd * MICRO:
                raise SpendCapExceeded(f"Meta gateway share of ${self.settings.meta_gateway_cap_usd} reached")
        if bucket == COMMUNITY:
            if (total.get("buckets") or {}).get(COMMUNITY, {}).get("cost_micro", 0) >= COMMUNITY_TOTAL_USD * MICRO:
                raise SpendCapExceeded(f"The free tier's ${COMMUNITY_TOTAL_USD} share is used up")
            day = (await self._ref(self._today()).get()).to_dict() or {}
            if (day.get("buckets") or {}).get(COMMUNITY, {}).get("cost_micro", 0) >= COMMUNITY_DAILY_USD * MICRO:
                raise SpendCapExceeded(f"The free tier's ${COMMUNITY_DAILY_USD} for today is used up; it resets at 00:00 UTC")

    async def record(self, model: str, cost: int, counts: Dict[str, int], bucket: Optional[str] = None,
                     response_id: Optional[str] = None, reserve: bool = False) -> None:
        """Book one call. With `response_id`, a response is booked once however often it is read.

        `reserve=True` books a worst-case hold for a queued background response;
        the final booking of the same response replaces the hold with the actual cost.
        """
        inc = firestore_mod.Increment
        now = datetime.now(timezone.utc)
        calls, charge, day = 1, cost, self._today()
        calls_ref = self.fs.db.collection(self.fs._collection("meta_spend_calls"))
        if response_id and not reserve:
            held = await calls_ref.document(f"{response_id}:reserve").get()
            if held.exists:
                hold = held.to_dict()
                calls = 0  # the hold already counted the call
                # No usage (failed or cancelled run): keep the hold, the tokens may have been spent.
                charge = cost - hold["cost_micro"] if any(counts.values()) else 0
                day = hold.get("day", day)  # settle on the day the hold was booked
        fields = {
            "cost_micro": inc(charge),
            "calls": inc(calls),
            "in_tokens": inc(counts.get("in_tokens", 0)),
            "out_tokens": inc(counts.get("out_tokens", 0)),
            "cached_tokens": inc(counts.get("cached_tokens", 0)),
            "models": {model: {
                "cost_micro": inc(charge),
                "calls": inc(calls),
                "in_tokens": inc(counts.get("in_tokens", 0)),
                "out_tokens": inc(counts.get("out_tokens", 0)),
            }},
            "updated_at": now,
        }
        if bucket:
            fields["buckets"] = {bucket: {"cost_micro": inc(charge), "calls": inc(calls)}}
        batch = self.fs.db.batch()
        if response_id:
            marker = calls_ref.document(f"{response_id}:reserve" if reserve else response_id)
            batch.create(marker, {"model": model, "cost_micro": cost, "at": now, "day": day})
        batch.set(self._ref(day), fields, merge=True)
        batch.set(self._ref("total"), fields, merge=True)
        try:
            await batch.commit()
        except AlreadyExists:
            return

        alert_micro = self.settings.meta_spend_alert_usd * MICRO
        total = (await self._ref("total").get()).to_dict() or {}
        if total.get("cost_micro", 0) < alert_micro:
            return
        alert_ref = self._ref("alert_100")
        for _ in range(MAX_ATTEMPTS):
            try:
                await alert_ref.create({"crossed_at": now, "total_micro_at_cross": total["cost_micro"]})
                break
            except AlreadyExists:
                return
            except Aborted:
                # Concurrent creates contend on one doc; whoever lost re-checks.
                if (await alert_ref.get()).exists:
                    return
        else:
            logger.error("meta_spend.alert_unrecorded total_micro=%d", total["cost_micro"])
        logger.error(
            "meta_spend.alert total_usd=%.2f crossed $%s (cap $%s)",
            total["cost_micro"] / MICRO, self.settings.meta_spend_alert_usd, self.settings.meta_spend_cap_usd,
        )


class MetaClient:
    """Responses and Chat Completions client. Use `get_meta_client()` in app code."""

    def __init__(self, settings: Optional[Settings] = None, guard: Optional[MetaSpendGuard] = None):
        self.settings = settings or get_settings()
        self.guard = guard or MetaSpendGuard(settings=self.settings)
        self._client = httpx.AsyncClient(timeout=120.0)

    async def _request(self, method: str, path: str, json: Optional[dict] = None) -> Dict[str, Any]:
        if not self.settings.meta_api_key:
            raise MetaAPIError("MODEL_API_KEY is not configured", status_code=503)
        url = f"{self.settings.meta_api_base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {self.settings.meta_api_key}"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.request(method, url, json=json, headers=headers)
            except httpx.TransportError as exc:
                # A connect failure never reached Meta; anything later may have been billed.
                sent = not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                logger.error("meta_api.transport %s %s sent=%s err=%r", method, path, sent, exc)
                raise MetaAPIError("Meta API unreachable", status_code=503, maybe_billed=sent) from exc
            if resp.status_code == 429 and attempt < MAX_ATTEMPTS:
                try:
                    wait = float(resp.headers.get("retry-after", ""))
                except ValueError:
                    wait = float(2 ** attempt)
                await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
                continue
            if resp.status_code == 400 and "content_policy_violation" in resp.text and attempt < MAX_ATTEMPTS:
                # Meta's prompt filter is flaky: the same harmless prompt is refused, then accepted. Not billed.
                logger.warning("meta_api.filtered %s %s attempt=%d, retrying", method, path, attempt)
                await asyncio.sleep(1.0)
                continue
            if resp.status_code != 200:
                logger.error("meta_api.error %s %s -> %d: %s", method, path, resp.status_code, resp.text[:500])
                raise MetaAPIError(f"Meta API returned {resp.status_code}", status_code=resp.status_code)
            return resp.json()
        raise AssertionError("unreachable")

    async def _record_if_done(self, model: str, data: Dict[str, Any], payload: Dict[str, Any],
                              bucket: Optional[str] = None) -> None:
        prompt = payload.get("messages", payload.get("input", ""))
        max_out = payload.get("max_output_tokens", payload.get("max_tokens", 0))
        if data.get("status", "completed") not in TERMINAL:
            if payload:  # just created and queued: hold the worst case until it finishes
                hold = cost_micro(model, {}, prompt_chars=len(str(prompt)), max_output_tokens=max_out, payload=payload)
                try:
                    await self.guard.record(model, hold, {}, bucket=bucket, response_id=data.get("id"), reserve=True)
                except Exception:
                    # The response exists and is billed: losing its id would mean a second paid call.
                    logger.exception("meta_spend.hold_failed response=%s cost_micro=%d", data.get("id"), hold)
            return
        cost = cost_micro(model, data, prompt_chars=len(str(prompt)), payload=payload, max_output_tokens=max_out)
        await self.guard.record(model, cost, _usage_counts(data), bucket=bucket, response_id=data.get("id"))

    async def _call(self, path: str, payload: Dict[str, Any], bucket: Optional[str]) -> Dict[str, Any]:
        model = payload["model"]
        await self.guard.check(model, bucket=bucket)
        try:
            data = await self._request("POST", path, json=payload)
        except MetaAPIError as exc:
            if exc.maybe_billed:
                # No response, so no id and no usage: book the conservative estimate.
                await self._record_if_done(model, {}, payload, bucket=bucket)
            raise
        await self._record_if_done(model, data, payload, bucket=bucket)
        return data

    async def create_response(self, payload: Dict[str, Any], *, bucket: Optional[str] = None) -> Dict[str, Any]:
        """POST /responses. A background response holds its worst-case cost until `get_response` sees it finish."""
        if payload.get("background") and not payload.get("max_output_tokens"):
            raise ValueError("a background response needs max_output_tokens, or its worst case is unbounded")
        return await self._call("/responses", payload, bucket=bucket)

    async def create_image(self, payload: Dict[str, Any], *, bucket: Optional[str] = None) -> Dict[str, Any]:
        """POST /images/generations (Muse Image). Billed per image, `n` of them."""
        return await self._call("/images/generations", payload, bucket=bucket)

    async def create_chat_completion(self, payload: Dict[str, Any], *, bucket: Optional[str] = None) -> Dict[str, Any]:
        """POST /chat/completions (used by the LLM gateway for `meta/` models)."""
        return await self._call("/chat/completions", payload, bucket=bucket)

    async def get_response(self, response_id: str, *, model: str) -> Dict[str, Any]:
        """GET /responses/{id}. A finished response is booked once, however often it is read."""
        data = await self._request("GET", f"/responses/{response_id}")
        await self._record_if_done(model, data, {})
        return data

    async def close(self) -> None:
        await self._client.aclose()


_client: Optional[MetaClient] = None


def get_meta_client() -> MetaClient:
    global _client
    if _client is None:
        _client = MetaClient()
    return _client
