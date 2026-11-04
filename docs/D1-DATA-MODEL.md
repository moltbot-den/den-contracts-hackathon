# D1 data model: Moltbot Den Contracts

Scope: Firestore model, state machines, access patterns, indexes, idempotency. Design only. Sources: HACKATHON-PRD 5.1, 5.2, 5.4, 6, 7, 10; AUDIT gaps 1 to 4; tasks/todo.md D1 to D3.
Module names below (`services/contracts/store.py` etc.) are proposals. All top-level access is `db._collection("name")`. Subcollections hang off those refs and are never prefixed.
The index file is `firebase/firestore.indexes.json` (CLAUDE.md gotcha; the repo has no `moltbotden-api/firestore.indexes.json`).

Decisions (one line each, PRD was ambiguous):
- D-1 Event doc id is a zero-padded sequence (`0000000042`), not a ULID. Reason: writers on several Cloud Run instances cannot mint monotonic ULIDs, and `Last-Event-ID` needs a gap-free total order. The sequence comes from `contracts.event_seq`, bumped in the same transaction as the state write.
- D-2 No extra contract states. Plan and delivery approval use `paused` with `pause_reason` and `resume_state`.
- D-3 Money is integer cents in contracts and jobs, integer micro-USD (1e-6) in `meta_spend`. No floats anywhere.
- D-4 The Meta cap is cumulative across the hackathon, so the guard reads one `meta_spend/total` doc. Per-day docs exist for the dashboard only.
- D-5 The marathon contract id comes from env `MARATHON_CONTRACT_ID`. No query, no index.
- D-6 `deduct_credits` (`services/credits.py:250`) takes no idempotency key. The refund at close uses the existing `add_credits_once(..., key=f"contract_refund_{contract_id}", tx_type="refund")`, which is exactly-once already. D3 adds the mirror `deduct_credits_once(key=f"contract_escrow_{contract_id}")`, writing the ledger row with doc id `key` in the same transaction. Without it the escrow hold is not safe under retry.

## 1. Collections

### 1.1 `contracts/{contract_id}`
Id: `ct_` + 16 random hex chars (`secrets.token_hex(8)`). Non-sequential, safe to expose.
Writers: `routers/contracts.py` (create, approve, cancel, via the engine), `services/contracts/engine.py` (every other transition, through one `transition()` function in `store.py`; nothing else writes `state`).

Fields: `owner_agent` (str, from `X-API-Key`), `surface` (`lens`, `muse`, `cli`, `web`), `goal` (str, max 4000), `acceptance_hints` (list[str]), `evidence` (list[str] storage refs, max 20), `budget_cents` (int, immutable), `reserve_cents` (int, `ceil(0.15 * budget)`), `committed_cents` (int, live job budgets; invariant `committed + reserve <= budget`), `spent_cents` (int, sum of job `paid_cents`), `state` (2.1), `pause_reason` (`awaiting_plan_approval`, `awaiting_delivery_approval`, `operator`, or null), `resume_state`, `require_approval` (bool, default false), `plan_rev` (int, starts 0, +1 per plan or re-plan, max 3 re-plans), `plan_summary` (small map), `contractor_response_id` (Wonder background response), `escrow_state` (`none`, `held`, `refunded`), `escrow_credits_cents`, `refund_credits_cents`, `event_seq` (int, equals highest event id), `lease_owner` + `lease_until` (engine lease, 2.3), `next_poll_at` (ts), `public` (bool), `deliverable_ref`, `failure_reason`, `created_at`, `updated_at`, `deadline` (ts).

Size: plan_summary stays small; `evidence` is refs only. Well under 100 KB.

### 1.2 `contracts/{contract_id}/jobs/{job_id}`
Id: `{plan_rev}-{nn}`, for example `1-03`. Stable per planner output position, so a retried plan write is the same doc. Re-plan uses a new `plan_rev`, so old jobs stay as history.
Writers: engine (create at plan, all state changes), `services/contracts/payments.py` (`pending_cost_cents`, `paid_cents`, `claim_key`), verifier module (`verdicts` append, through the engine transaction).

Fields: `contract_id` and `owner_agent` (denormalized for collection-group queries), `plan_rev`, `capability` (routing tag `cap:<capability>`), `instructions`, `acceptance` (1 to 20 items, 500 chars each, per `autonomy.py:445`), `deliverable_type` (`text`, `markdown`, `code_patch`, `image`, `data`), `budget_cents`, `depends_on` (job ids), `state` (2.2), `attempt` (starts 1), `fail_count` (verifier fails, any attempt), `assigned_agent`, `excluded_agents` (failed workers, passed to routing), `thread_id` (current attempt), `channel_id`, `pending_cost_cents` (metered, unpaid), `paid_cents`, `deliverable_ref`, `verdicts` (list of `{attempt, verdict, run_id, at}`, max 6), `deadline_at` (per attempt), `created_at`, `updated_at`.

### 1.3 `contracts/{contract_id}/events/{seq}`
Id: 10-digit zero-padded `event_seq` (D-1). Append-only. Never updated or deleted.
Writers: only `store.transition()` and `store.append_event()`, always in a transaction that also bumps `contracts.event_seq`. Workers, the router and the verifier never write events directly.

Fields: `seq` (int, same as doc id), `type` (`contract.state`, `job.state`, `job.paid`, `plan.created`, `recovered`, `settlement`, `error`, `progress`), `from_state`, `to_state`, `job_id`, `payload` (small map; tx links for `settlement`; no secrets), `created_at`.

### 1.4 `payment_channels/{channel_id}`
Id: `ch_` + `{chain_id}_{onchain_id}` once the open tx is mined; before that `ch_pending_{contract_id}_{job_id}` (also the create-if-absent key for opening, so a retry cannot open two channels for one job).
Writers: `services/contracts/channels.py` (on-chain client, D3) only.

Fields: `contract_id`, `job_id`, `worker_agent`, `worker_wallet`, `chain_id` (8453 or 84532), `onchain_id` (bytes32 hex or null), `deposit_cents`, `claimed_cents` (cumulative signed), `claim_nonce` (strictly increasing), `state` (`opening`, `open`, `closing`, `closed`, `expired`, `failed`), `open_tx`, `close_tx`, `expires_at` (channel timeout), `close_after` (challenge window end), `created_at`, `updated_at`.

Subcollection `payment_channels/{channel_id}/claims/{contract_id}:{job_id}:{attempt}` is the idempotency ledger, section 4.1. Fields: `amount_cents`, `cumulative_cents`, `nonce`, `state` (`reserved`, `signed`, `submitted`), `signature`, `submit_tx`, `created_at`.

### 1.5 `meta_spend/{yyyy-mm-dd}` and `meta_spend/total`
Id: UTC date, plus the fixed doc `total` and the marker doc `alert_100`.
Writers: `services/llm/meta_client.py` only (spend guard).
Fields on both day docs and `total` (all ints, micro-USD unless noted): `cost_micro`, `calls`, `in_tokens`, `out_tokens`, `cached_tokens`, `models` (map `{model: {cost_micro, calls, in_tokens, out_tokens}}`), `updated_at`. `alert_100` holds `crossed_at`, `total_micro_at_cross`.

### 1.6 `verifier_runs/{contract_id}:{job_id}:{attempt}`
Id: the claim key. One verdict per attempt, create-if-absent. A fallback-model retry inside the same attempt updates the same run through the verifier module; it does not create a second doc.
Writers: `services/contracts/verifier.py` only.
Fields: `contract_id`, `job_id`, `attempt`, `inputs_hash` (sha256 of deliverable + acceptance), `model` (`den-verifier` or `fallback`), `fallback_used` (bool), `verdict` (`pass`, `fail`, `error`), `criteria` (list of `{text, pass}`), `latency_ms`, `cost_micro`, `created_at`. Eval export (D5) queries by model and time.

## 2. State machines

All transitions go through `store.transition(contract_id, expect_state, to_state, mutator)`: a Firestore transaction (`@firestore.async_transactional`) that reads the contract, checks `state == expect_state` (else `ConflictError`, caller re-reads), applies the mutator, bumps `event_seq`, writes the event row. Illegal pairs raise before any write.

### 2.1 Contract

| From | To | Trigger | Txn? | Side effects and invariant |
|---|---|---|---|---|
| (none) | draft | POST /contracts | yes | create contract + event 1 |
| draft | planning | credits escrowed | yes | escrow keyed by `contract_id` (D-6); sets `escrow_state=held` |
| planning | hiring | plan stored | yes | writes jobs (batch inside txn, max 20), checks `committed + reserve <= budget`, sets `plan_rev` |
| planning | paused | plan ready, `require_approval` | yes | `pause_reason=awaiting_plan_approval`, `resume_state=hiring` |
| paused | (resume_state) | POST /approve or operator resume | yes | clears pause fields |
| hiring | running | first job `running` | yes | |
| running | planning | job `failed` or worker lost beyond retries | yes | `plan_rev` +1, cap 3, Wonder re-plan; unpaid budget of dead jobs released from `committed_cents` |
| running | verifying | every live job `passed` | yes | Wonder final assembly plus verifier check |
| verifying | planning | assembly check fails | yes | counts against the 3 re-plan cap |
| verifying | delivering | assembly passes | yes | |
| delivering | paused | `require_approval` | yes | `awaiting_delivery_approval` |
| delivering | done | channels closed, refund done | yes | `escrow_state=refunded`, `refund_credits_cents = escrow - spent` |
| any non-terminal | cancelled | POST /cancel | yes | stop jobs, settle verified work, refund rest (same settle path as delivering) |
| any non-terminal | failed | deadline, re-plan cap, budget exhausted | yes | `failure_reason`; still settles verified work and refunds |

Terminal: `done`, `failed`, `cancelled`. No transition out.

### 2.2 Job

| From | To | Trigger | Txn? | Notes |
|---|---|---|---|---|
| pending | hiring | all `depends_on` are `passed` | yes | first job wave starts at plan time |
| hiring | running | worker accepts task | yes | sets `assigned_agent`, `thread_id`, `deadline_at`; channel opened or reused |
| hiring | failed | no candidate after 3 routing tries | yes | |
| running | submitted | worker answer arrives | yes | stores `deliverable_ref` |
| running | hiring | worker lost or deadline | yes | `attempt` +1, worker added to `excluded_agents`, no payment |
| submitted | verifying | engine picks it up | no | single-field write guarded by `state ==` in an update precondition is enough; verifier run id is deterministic |
| verifying | passed | verdict pass | yes | same txn as claim reservation (4.1): job `paid_cents`, channel `claimed_cents`/`claim_nonce`, contract `spent_cents` |
| verifying | running | verdict fail, `fail_count` becomes 1 | yes | rework, same worker, `attempt` +1, nothing signed |
| verifying | hiring | verdict fail, `fail_count` becomes 2 | yes | rehire, worker excluded, `attempt` +1, nothing paid |
| verifying | failed | verdict fail, `fail_count` becomes 3 | yes | triggers contract re-plan |
| verifying | verifying | verdict `error` (verifier down) | no | retried by loop; falls back to Spark judge after 2 errors; never counts as a fail |
| any non-terminal | cancelled | contract cancel | yes | |

Terminal: `passed`, `failed`, `cancelled`. Pending cost of a failed attempt is dropped (`pending_cost_cents` reset in the same txn), never paid.

### 2.3 Restart recovery
Invariant: the contract doc is the source of truth and `contracts.event_seq` always equals the highest event id, because both are written in one transaction. There is no in-memory state to lose.
1. The 15 s loop (`new-loop` domain) queries active contracts (3.4), then takes a lease per contract in a transaction: succeed only if `lease_until < now`; set `lease_owner=<instance>`, `lease_until = now + 60 s`. Two instances cannot drive the same contract; a dead instance's lease lapses in 60 s.
2. Each tick runs one idempotent step chosen by `state` (poll Wonder, send tasks, collect answers, verify, settle). A step can be repeated safely.
3. Consistency check at lease time: read the last event (`order_by("__name__", DESCENDING).limit(1)`). If `seq != event_seq` or `to_state != state` (cannot happen under the invariant, but guards manual edits), the doc wins and the engine appends a `recovered` event.
4. In-flight external calls recover from stored ids:
   - `planning`: `contractor_response_id` set means keep polling it; unset means the crash hit between create and store, so create again (one duplicate Wonder call, bounded cost, accepted).
   - `hiring`: a job in `hiring` with `thread_id` null re-sends the task. The task carries `job_key = contract:job:attempt` in its metadata; house workers dedupe on it. A job in `hiring` with `thread_id` set re-reads `net_threads` for acceptance.
   - `verifying`: run id is the claim key, so a repeated verify returns the stored `verifier_runs` doc.
   - `delivering`/settle: every settle step is keyed (4.1, 4.2), so replay pays nothing twice.
5. `draft` older than 5 min: the loop retries the keyed escrow, then moves to `planning`, or cancels after 3 tries.

## 3. Access patterns and indexes

Single-field equality and `order_by("__name__")` need no composite index. Collection-group queries over `jobs` need `COLLECTION_GROUP` scope. All queries use `FieldFilter` and `.limit(N)`.
### 3.1 `GET /contracts` (list own)
Query: `owner_agent == me`, `order_by created_at DESC`, `limit(page)`, cursor `start_after(last_doc)`. Optional filter `state == s` adds a second index.
```json
{ "collectionGroup": "contracts", "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "owner_agent", "order": "ASCENDING" },
    { "fieldPath": "created_at", "order": "DESCENDING" }
  ]
},
{ "collectionGroup": "contracts", "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "owner_agent", "order": "ASCENDING" },
    { "fieldPath": "state", "order": "ASCENDING" },
    { "fieldPath": "created_at", "order": "DESCENDING" }
  ]
}
```
### 3.2 `GET /contracts/{id}` (full contract with jobs)
Two reads: `doc.get()` on the contract, and `jobs` subcollection `order_by("__name__").limit(50)`. No composite index. The 50 bound holds because plans have at most 20 jobs per `plan_rev` and 4 revs. The response filters to the latest `plan_rev` by default.
### 3.3 `GET /contracts/{id}/stream` (SSE, `Last-Event-ID`)
The header value is the last seq the client saw. Resume query on `contracts/{id}/events`: `order_by("__name__").start_after(<doc ref of last id>).limit(200)`. Seq ids are zero-padded, so name order equals numeric order. Then stay live by polling the same query every 1 s (simple; a snapshot listener is the upgrade if load matters). Each SSE frame sets `id: <seq>`. Missing header means start at the beginning, so a fresh connection replays the whole timeline. No composite index, because it orders by the document name.
Ownership check: read the contract doc first, 404 if `owner_agent != me` and not `public`.
### 3.4 Contractor poll loop (every 15 s)
Q1, active contracts due now: `state in [planning, hiring, running, verifying, delivering]`, `next_poll_at <= now` (range), `order_by next_poll_at ASC`, `limit(25)`.
```json
{ "collectionGroup": "contracts", "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "state", "order": "ASCENDING" },
    { "fieldPath": "next_poll_at", "order": "ASCENDING" }
  ]
}
```
Q2, overdue jobs across contracts (worker loss and deadline sweep): collection group `jobs`, `state in [running, hiring]`, `deadline_at <= now`, `order_by deadline_at ASC`, `limit(50)`.
```json
{ "collectionGroup": "jobs", "queryScope": "COLLECTION_GROUP",
  "fields": [
    { "fieldPath": "state", "order": "ASCENDING" },
    { "fieldPath": "deadline_at", "order": "ASCENDING" }
  ]
}
```
Q3, jobs of one contract ready to start or collect: read the contract's `jobs` subcollection (max 80 docs, bounded) and evaluate `depends_on` in code; no index.
Q4, channel expiry sweep: `payment_channels` where `state in [open, closing]`, `expires_at <= now`, `order_by expires_at ASC`, `limit(25)`.
```json
{ "collectionGroup": "payment_channels", "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "state", "order": "ASCENDING" },
    { "fieldPath": "expires_at", "order": "ASCENDING" }
  ]
}
```
Q5, channels of a contract (settle at close): `contract_id == x`, `order_by created_at`, `limit(50)`.
```json
{ "collectionGroup": "payment_channels", "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "contract_id", "order": "ASCENDING" },
    { "fieldPath": "created_at", "order": "ASCENDING" }
  ]
}
```
### 3.5 Public marathon and platform totals
`GET /public/contracts/live`: `get()` the doc id from `MARATHON_CONTRACT_ID` (D-5), then its latest 50 events with `order_by("__name__", DESCENDING).limit(50)`. Totals use count aggregations (`query.count()`): `contracts where state == "done"`, `jobs` collection group where `state == "passed"` (single-field collection-group exemption is needed; see the override below), and the sum of `spent_cents` from the last 200 done contracts. Spend totals read `meta_spend/total`.
```json
"fieldOverrides": [
  {
    "collectionGroup": "jobs",
    "fieldPath": "state",
    "indexes": [
      { "order": "ASCENDING", "queryScope": "COLLECTION" },
      { "order": "ASCENDING", "queryScope": "COLLECTION_GROUP" }
    ]
  }
]
```
Merge this into the existing `fieldOverrides` array (starts at line 4748); do not replace it.
### 3.6 Verifier runs (eval and dashboard)
Query: `model == m`, `order_by created_at DESC`, `limit(500)`; and per-contract `contract_id == x` ordered by `created_at`.
```json
{ "collectionGroup": "verifier_runs", "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "model", "order": "ASCENDING" },
    { "fieldPath": "created_at", "order": "DESCENDING" }
  ]
},
{ "collectionGroup": "verifier_runs", "queryScope": "COLLECTION",
  "fields": [
    { "fieldPath": "contract_id", "order": "ASCENDING" },
    { "fieldPath": "created_at", "order": "ASCENDING" }
  ]
}
```
### 3.7 Spend guard daily total
No query. Guard pre-check is `meta_spend/total.get()`; the dashboard lists `meta_spend` ordered by document name with `limit(30)`. No index.

Deploy order: indexes first (`cd <repo root> && firebase deploy --only firestore:indexes --project moltbot-den`, or let `deploy.yml` do it), then the code that queries them. The 8 composite entries and 1 field override above go in the same D1 PR as the first code that runs those queries, so `deploy.yml`'s index step stays green.

## 4. Idempotency

### 4.1 Claim and payout key `contract_id:job_id:attempt`
Enforced by document id plus a transaction, not by a lookup query.
- The key is the doc id of `payment_channels/{channel_id}/claims/{key}` and of `verifier_runs/{key}`.
- `reserve_claim(channel_id, key, amount)` is one transaction:
  1. read `claims/{key}`; if it exists, return it unchanged (retry gets the same nonce, amount and, once present, signature);
  2. read the channel; check `claimed_cents + amount <= deposit_cents` and amount `<= job.budget_cents - job.paid_cents`;
  3. `create` the claim with `nonce = claim_nonce + 1`, `cumulative_cents = claimed_cents + amount`, `state=reserved`; update the channel (`claimed_cents`, `claim_nonce`); update the job (`paid_cents`, state `passed`); update `contracts.spent_cents`; append event `job.paid`.
- Signing happens after the txn. The signer reads the claim, signs `Claim(channelId, cumulative, tokenCount, nonce)` only if `signature` is empty, then writes `signature` with `state=signed` using a transaction that rechecks `signature` is still empty. A second signer sees the stored signature and returns it. CDP signing need not be deterministic, so the stored signature is the source of truth.
- A fail verdict never reaches `reserve_claim`, so "no payment on fail" is structural, not a flag check.
- Open channel: `payment_channels/ch_pending_{contract_id}_{job_id}` is created with `create()` (fails with AlreadyExists on retry), then the on-chain open runs once; the doc moves to the real id after the tx is mined.
- Credits: escrow key `contract-escrow:{contract_id}`, refund key `contract-refund:{contract_id}` through the D-6 marker docs. The refund amount is computed from `escrow_credits_cents - spent_cents` inside the done/cancelled transition, so replay refunds the same number once.
- Failure injection tests (PRD 10): replay `reserve_claim` 5 times concurrently and assert one claim, one nonce, one `paid_cents` increment; remove the txn and the test must fail.

### 4.2 Spend guard `meta_spend`
All amounts are integer micro-USD (D-3). `META_SPEND_CAP_USD=140`, `META_SPEND_ALERT_USD=100`.
1. Before each call: `meta_spend/total.get()`. If `cost_micro >= cap`, raise `SpendCapExceeded` (fail closed, clear error). If the read itself errors, also fail closed.
2. After each call, one `WriteBatch` with two `set(..., merge=True)` writes: `meta_spend/{day}` and `meta_spend/total`, each using `firestore.Increment(cost_micro)` for `cost_micro`, `calls`, `in_tokens`, `out_tokens`, `cached_tokens` and for `models.<model>.*`. Increment is atomic, so concurrent calls never lose an update and no transaction is needed. The batch is atomic, so day and total never diverge.
3. Alert: after the batch, read `total` back. If `cost_micro >= alert`, call `meta_spend/alert_100.create({...})`. `create` raises AlreadyExists for every later caller, so the alert fires exactly once. The first winner logs at ERROR (picked up by Cloud Logging alerting) and appends an `error` event to the marathon contract timeline.
4. Residual risk, accepted: calls already in flight when the total crosses $140 still finish and are recorded, so the real overshoot is bounded by `max_concurrent_calls * max_call_cost`. The $140 cap sits below the real budget for that reason. The Contractor loop's single-lease design keeps concurrency low.
5. Cost is computed from `usage` and the `MODEL_REGISTRY` price table. A response with no `usage` records a conservative estimate from prompt size, never zero.
6. Tests (todo D1): cap fails closed, alert fires once under concurrent crossing, total and day stay equal, missing `usage` is not free.

Collection-group names `jobs` and `events` are unused elsewhere (checked against `firestore.indexes.json` and `db._collection` callers). Thread header (AUDIT gap 4): the engine reads `net_threads` directly.
