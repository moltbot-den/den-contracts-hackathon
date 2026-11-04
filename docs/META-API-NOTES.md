# Meta Model API notes for the D1 client (2026-10-22)

Base: https://api.meta.ai/v1 (OpenAI SDK compatible). 12 live calls used, no sam-3.1.
Docs root: https://dev.meta.ai/docs/<page>.md (the .md suffix returns raw markdown, curl works).
Tags: VERIFIED = observed, DOCS = doc says, UNKNOWN.

## 1. Rate-limit headers and 429
- DOCS: every successful token response carries `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens`,
  `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`. Source: /docs/pricing-rate-limits
- VERIFIED (contradicts docs): none of these appeared on 5 chat/responses calls with this key. Headers seen were only:
  `content-type, x-request-id, access-control-allow-origin, date, x-route (model-api-rust), connection, content-length`.
  The client must treat the ratelimit headers as optional. Log `x-request-id` for support.
- Example header values: UNKNOWN (never observed).
- 429 Retry-After: DOCS says yes, wait at least that long, else exponential backoff from 500 ms with jitter; background
  creation over 600/min/team also 429s with Retry-After. VERIFIED earlier (AUDIT.md s7): sam-3.1 429
  `rate_limit_exceeded` "Service temporarily unavailable" with `retry-after: 5`.
- Limits (DOCS, per team not per key, per model): Wonder, Spark 1.3: 3,000 RPM, 4,000,000 TPM.
  spark-1.3-contributor: 150 RPM, 3,000,000 TPM. Background submissions: 600/min/team extra.
- Error envelope (DOCS /docs/error-handling): `{"error":{"message","type","param","code"}}`, type is
  invalid_request_error | authentication_error | rate_limit_error | server_error. Unknown path = 404 with empty body.
  Non-streaming past the server time limit = 504 `gateway_timeout`.

## 2. Usage fields (Spark)
- VERIFIED chat completions (`muse-spark-1.3`):
  `usage.prompt_tokens`, `completion_tokens`, `total_tokens`,
  `completion_tokens_details.reasoning_tokens` (e.g. 177 of 187 completion tokens for a reply of "ok"),
  `prompt_tokens_details.cached_tokens` and `.cache_write_tokens` (null). Reasoning is INSIDE completion_tokens.
- VERIFIED Responses API: `usage.input_tokens`, `output_tokens`, `total_tokens`,
  `input_tokens_details.cached_tokens` (+ `cache_write_tokens` null), `output_tokens_details.reasoning_tokens`
  (398 of 400 when max_output_tokens=400 was exhausted).
- Reasoning is not cheap or small: "Say ok." burned 177 to 398 reasoning tokens. Budget max_tokens >= 400 for tiny asks.
  Responses `max_output_tokens` minimum is 16 (DOCS).
- Billing: cached_tokens is a SUBSET of input tokens (DOCS). Cost = (input - cached)*in + cached*cached_rate + output*out
  where output includes reasoning tokens.

## 3. Pricing per 1M tokens (DOCS, https://dev.meta.ai/docs/pricing-rate-limits)
| Model | Input | Cached input | Output |
|---|---|---|---|
| muse-wonder-1.0 (Standard only) | $4.00 | $0.20 | $20.00 |
| muse-spark-1.3 / 1.2 / 1.1 (Standard) | $1.25 | $0.15 | $4.25 |
| muse-spark-1.3-contributor (trains on your data) | $0.10 | $0.002 | $0.20 |
- No long-context premium. Web search grounding: $2.50 per 1,000 queries on top of tokens.
- Other: Muse Image $0.01/image, Voice Transcribe $0.18/hour, SAM 3.1 $2.50/1k images, $0.20/1k video frames.
- VERIFIED cross-check: AUDIT.md spike 14 in / 173 out = $0.00075 matches Spark standard rates.
- No separate cache-write price listed (usage shows cache_write_tokens, null so far).

## 4. Wonder background responses
- DOCS (/docs/protocols/responses): `background: true` on `POST /v1/responses`; ack has `status: "queued"`, `background: true`.
  Poll `GET /v1/responses/{id}` until status is not queued/in_progress (completed, failed, incomplete, cancelled).
  `GET /v1/responses/{id}?stream=true` is an SSE lifecycle stream (response.created, terminal event, [DONE]); resume with
  `starting_after`. Cancel: `POST /v1/responses/{id}/cancel`. Cannot combine with `stream: true` (400).
  Body cap 1 MiB (413 payload_too_large): use file_id for large media. 600 submissions/min/team.
- VERIFIED BLOCKED: 3 attempts with `background=True` on muse-wonder-1.0 returned
  `400 invalid_request_error: "This API surface is not available for subscription accounts. Switch to PAYG mode or use
  Chat Completions / Responses."` The same model with `background=False` on /responses works (status completed,
  output "pong", 125 reasoning tokens). So this key's account is a subscription account and background mode is gated.
  End-to-end run NOT achieved. Need PAYG billing on the Meta team, or fall back to streaming/non-streaming Responses.
```python
r = client.responses.create(model="muse-wonder-1.0", input="...", background=True, max_output_tokens=2000)
while r.status in ("queued", "in_progress"):
    time.sleep(3); r = client.responses.retrieve(r.id)   # then r.output_text, r.usage
```
(snippet is DOCS-shaped, unverified past the 400 above)

## 5. Prompt caching
- DOCS (/docs/prompt-caching): automatic prefix caching, no flag needed. Optional `prompt_cache_key` (chat and responses)
  routes similar requests together to raise hit rate; use a stable app-level string. Put stable content first.
  Independent of `store`. Hits show at chat `usage.prompt_tokens_details.cached_tokens`, Responses
  `usage.input_tokens_details.cached_tokens`. Zero is normal on first request or after eviction.
- Minimum prefix and TTL: UNKNOWN (docs give none; their example caches 1,792 of 1,847 tokens, suggesting block granularity).
- VERIFIED no hit in our tests: Spark chat, 2,029-token identical prompt, 3 repeats (one with prompt_cache_key,
  one after 5 s): cached_tokens 0 each time. Spark Responses, 8,426-token identical `instructions`, 2 calls 8 s apart:
  cached_tokens 0 both. So caching is not reliably observable on this key; do not assume hits in the spend guard.
  Price conservatively with cached_tokens as reported (0) and read the field when present.

## 6. Structured output
- DOCS (/docs/structured-output): chat `response_format={"type":"json_schema","json_schema":{"name":..,"schema":..}}`;
  Responses uses `text.format` instead. Decoding is constrained. Recursive schemas return 400.
- VERIFIED Spark: valid JSON `{"city":"Menlo Park","zip":"94025"}` (338 reasoning tokens, max_tokens=500).
- VERIFIED Wonder: accepted and constrained, but 481 of 500 tokens went to reasoning so output was truncated mid-JSON
  (`{\n "city": "Menlo Park",\n `). Truncation yields invalid JSON, so use max_tokens >= 2000 for Wonder
  and always json.loads inside try/except (finish_reason "length" means retry bigger).
```python
rf = {"type": "json_schema", "json_schema": {"name": "Addr", "schema": {
    "type": "object", "properties": {"city": {"type": "string"}, "zip": {"type": "string"}},
    "required": ["city", "zip"], "additionalProperties": False}}}
o = client.chat.completions.create(model="muse-spark-1.3", messages=msgs, response_format=rf, max_tokens=1500)
data = json.loads(o.choices[0].message.content)
```

## Client takeaways
- Floor max_tokens high (reasoning counts against it); content can be None if exhausted.
- Do not depend on ratelimit headers or cache hits; honor Retry-After when present, else backoff with jitter.
- `max_retries=0` on the SDK and own the retry loop so the spend guard sees every attempt.
- Reasoning tokens are billed as output: Spark ~$0.0017 per 400 reasoning tokens, Wonder ~$0.008.
