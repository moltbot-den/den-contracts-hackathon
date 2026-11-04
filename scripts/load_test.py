#!/usr/bin/env python3
"""
Load numbers for the hackathon README (PRD 8, D8). Two parts, neither spends Meta credit:

  http    paced GETs against the public contract endpoints in prod (/public/contracts/live and
          one contract page): latency of the 200s and a count per status. The API limits each
          IP to 100 requests a minute, so one client measures latency under that limit (default
          1.5 req/s); push --rps past it to see the limiter answer 429 with Retry-After.
  engine  N contracts driven to done by the real engine and house workers on the Firestore
          emulator, with Meta scripted (tests/test_contracts_engine.py FakeMeta): wall time,
          ticks, and per-tick latency. Run it under scripts/test-api.sh so the emulator is up.

Usage:
  python3 scripts/hackathon/load_test.py http --contract ct_285f6495e5b9dfcd --seconds 60
  scripts/test-api.sh --co -q >/dev/null; FIRESTORE_EMULATOR_HOST=localhost:8080 \\
    moltbotden-api/.venv/bin/python scripts/hackathon/load_test.py engine --contracts 25
"""

import argparse
import asyncio
import json
import statistics
import sys
import time


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


async def http(api: str, contract: str, concurrency: int, seconds: int, rps: float) -> dict:
    from collections import Counter

    import httpx

    paths = ["/public/contracts/live", f"/public/contracts/{contract}"]
    lat, statuses, n = [], Counter(), 0
    deadline, gap = time.monotonic() + seconds, concurrency / rps

    async def worker(client, i):
        nonlocal n
        while time.monotonic() < deadline:
            t = time.monotonic()
            try:
                r = await client.get(api + paths[(i + n) % 2])
                statuses[r.status_code] += 1
                if r.status_code == 200:
                    lat.append((time.monotonic() - t) * 1000)
            except httpx.HTTPError as e:
                statuses[type(e).__name__] += 1
            n += 1
            await asyncio.sleep(max(0.0, gap - (time.monotonic() - t)))

    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "den-load-test/1.0"}) as client:
        await asyncio.gather(*(worker(client, i) for i in range(concurrency)))
    return {"part": "http", "requests": n, "rps": round(n / seconds, 1), "statuses": dict(statuses),
            "ok_p50_ms": round(_pct(lat, .5)), "ok_p95_ms": round(_pct(lat, .95)), "ok_p99_ms": round(_pct(lat, .99)),
            "concurrency": concurrency, "seconds": seconds}


async def engine(contracts: int, minutes: float, tick_seconds: float) -> dict:
    sys.path.insert(0, "moltbotden-api")

    from services.contracts import workers
    from services.contracts.engine import Engine
    from services.firestore_client import get_firestore_client
    from services.net.service import NetworkService
    from tests.test_contracts_engine import FakeMeta, _buyer, _house, _store

    svc, store, meta = NetworkService(get_firestore_client()), _store(), FakeMeta()
    await _house(svc)
    eng = Engine(store=store, svc=svc, meta=meta, owner="load-test")
    ids = [(await store.create_contract(owner_agent=await _buyer(f"load{i}"), surface="cli",
                                        goal=f"Load {i}", budget_cents=2_000))["id"] for i in range(contracts)]
    started, ticks, tick_ms = time.monotonic(), 0, []
    # Real time matters: the network's 60-sends-per-minute limit paces the Contractor and the workers.
    while time.monotonic() - started < minutes * 60:
        await workers.serve_once(svc=svc, meta=meta)
        t = time.monotonic()
        await eng.tick()
        tick_ms.append((time.monotonic() - t) * 1000)
        ticks += 1
        states = [(await store.get(i))["state"] for i in ids]
        if all(s in ("done", "failed") for s in states):
            break
        await asyncio.sleep(tick_seconds)
    stuck = {}
    for i in ids:
        doc = await store.get(i)
        if doc["state"] not in ("done", "failed"):
            jobs = await store.jobs(i)
            stuck[i] = [doc["state"], [(j["capability"], j["state"], j.get("recipients"), j.get("thread_id"))
                                       for j in jobs], (await store.events(i))[-1]["type"]]
    inbox = {w: len(await svc.inbox(w, mark_read=False)) for w in workers.HOUSE_WORKERS}
    return {"part": "engine", "unread": inbox, "contracts": contracts, "done": states.count("done"), "failed": states.count("failed"),
            "stuck": stuck,
            "ticks": ticks, "wall_s": round(time.monotonic() - started, 1),
            "tick_p50_ms": round(statistics.median(tick_ms)), "tick_p95_ms": round(_pct(tick_ms, .95))}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("part", choices=["http", "engine"])
    p.add_argument("--api", default="https://api.moltbotden.com")
    p.add_argument("--contract", default="ct_285f6495e5b9dfcd")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--rps", type=float, default=1.5)
    p.add_argument("--seconds", type=int, default=30)
    p.add_argument("--contracts", type=int, default=25)
    p.add_argument("--minutes", type=float, default=10)
    p.add_argument("--tick-seconds", type=float, default=2)
    a = p.parse_args()
    out = asyncio.run(http(a.api, a.contract, a.concurrency, a.seconds, a.rps) if a.part == "http" else engine(a.contracts, a.minutes, a.tick_seconds))
    print(json.dumps(out))
