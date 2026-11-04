#!/usr/bin/env python3
"""
Build the Den Verifier dataset (hackathon PRD 5.5).

1. `generate`: muse-spark-1.3-contributor writes synthetic jobs across the five
   capabilities. Each job has 3 to 5 acceptance criteria, one good deliverable
   and two or three defective ones, each with one injected defect that breaks
   a known criterion. Labels come from construction: the good deliverable
   meets every criterion; a defective one misses exactly its injected one.
2. `check`: the same cheap model judges every example blind; examples where it
   disagrees with the construction label are dropped (PRD: disagreements dropped).
3. `gold`: muse-wonder-1.0 judges a stratified 300-example gold set, used to
   report teacher agreement and as the eval anchor.
4. `split`: 80/10/10 stratified by capability, chat-format JSONL for training.

Every Meta call goes through services/meta_client.py, so the spend guard
(cap, alert, booking) applies. Output goes to --out (default data/den-verifier,
gitignored); the dataset is pushed to Hugging Face separately.

Usage (from moltbotden-api/, PAYG key in META_API_KEY):
  python ../scripts/hackathon/verifier_data.py generate --jobs 2000
  python ../scripts/hackathon/verifier_data.py check
  python ../scripts/hackathon/verifier_data.py gold --n 300
  python ../scripts/hackathon/verifier_data.py split
"""

import argparse
import asyncio
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, ".")
from services.contracts.planner import CAPABILITIES  # noqa: E402
from services.meta_client import MetaAPIError, SpendCapExceeded, get_meta_client  # noqa: E402

GEN_MODEL = "muse-spark-1.3-contributor"
GOLD_MODEL = "muse-wonder-1.0"
DEFECTS = ("missing_criterion", "wrong_fact", "broken_code", "off_spec_format", "hallucinated_source")
DOMAINS = (
    "home repair", "pool and spa equipment", "cars", "bicycles", "kitchen appliances", "gardening", "small business",
    "travel planning", "personal finance basics", "fitness", "pets", "electronics", "woodworking", "plumbing",
    "HVAC", "photography", "music gear", "web development", "data analysis", "devops", "mobile apps", "education",
    "event planning", "real estate listings", "marketing copy", "legal paperwork basics", "medical device manuals",
    "boats", "camping gear", "3D printing",
)

SYSTEM = (
    "You are Den Verifier. Given a task, its acceptance criteria and a worker's deliverable, decide for each "
    "criterion whether the deliverable meets it, judging only from the deliverable. Answer with JSON: "
    '{"verdict": "pass"|"fail", "per_criterion": [{"criterion": int, "met": bool, "reason": str}], '
    '"confidence": number}. The verdict is pass only if every criterion is met.'
)

GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {"type": "string"},
        "acceptance": {"type": "array", "items": {"type": "string"}},
        "good": {"type": "string"},
        "defective": {"type": "array", "items": {"type": "object", "properties": {
            "defect": {"type": "string", "enum": list(DEFECTS)},
            "breaks_criterion": {"type": "integer"},
            "deliverable": {"type": "string"},
        }, "required": ["defect", "breaks_criterion", "deliverable"], "additionalProperties": False}},
    },
    "required": ["task", "acceptance", "good", "defective"],
    "additionalProperties": False,
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"per_criterion": {"type": "array", "items": {"type": "object", "properties": {
        "criterion": {"type": "integer"}, "met": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["criterion", "met", "reason"], "additionalProperties": False}}},
    "required": ["per_criterion"],
    "additionalProperties": False,
}


def gen_prompt(capability: str, domain: str, n_defects: int) -> str:
    return (
        f"Write one realistic job a buyer would hire a '{capability}' agent for, in the domain '{domain}'.\n"
        "Give 3 to 5 acceptance criteria, each specific and checkable from the deliverable alone (0-based order).\n"
        "Then write a GOOD deliverable that meets every criterion (concise, 80 to 300 words, or code if the job is code).\n"
        f"Then write {n_defects} DEFECTIVE deliverables. Each is a near-copy of the good one with exactly ONE injected "
        f"defect of a different type from: {', '.join(DEFECTS)}. The defect must make exactly one criterion fail; "
        "give its index as breaks_criterion. All other criteria must still be met. Do not mention the defect."
    )


def user_msg(task: str, acceptance: list, deliverable: str) -> str:
    crit = "\n".join(f"{i}. {c}" for i, c in enumerate(acceptance))
    return f"Task: {task}\n\nAcceptance criteria:\n{crit}\n\nDeliverable:\n{deliverable}"


async def call(payload: dict) -> dict:
    data = await get_meta_client().create_chat_completion(payload)
    return json.loads(data["choices"][0]["message"]["content"] or "")


async def generate(out: Path, jobs: int, concurrency: int) -> None:
    path = out / "raw.jsonl"
    done = sum(1 for _ in path.open()) if path.exists() else 0
    rng, gate, lock = random.Random(26), asyncio.Semaphore(concurrency), asyncio.Lock()
    plan = [(CAPABILITIES[i % len(CAPABILITIES)], rng.choice(DOMAINS), rng.choice((2, 3))) for i in range(jobs)]

    async def one(i, capability, domain, n_defects):
        async with gate:
            try:
                job = await call({"model": GEN_MODEL, "max_tokens": 12_000,
                                  "messages": [{"role": "user", "content": gen_prompt(capability, domain, n_defects)}],
                                  "response_format": {"type": "json_schema",
                                                      "json_schema": {"name": "Job", "schema": GEN_SCHEMA}}})
            except (json.JSONDecodeError, KeyError, MetaAPIError) as e:
                print(f"job {i} skipped: {e}", file=sys.stderr)
                return
            n = len(job.get("acceptance") or [])
            job["defective"] = [d for d in job.get("defective") or [] if 0 <= d.get("breaks_criterion", -1) < n]
            if not 3 <= n <= 5 or not job["defective"]:
                return
            async with lock:
                with path.open("a") as f:
                    f.write(json.dumps({"id": i, "capability": capability, "domain": domain, **job}) + "\n")

    await asyncio.gather(*(one(i, *p) for i, p in enumerate(plan) if i >= done))


def examples(out: Path):
    """Construction-labelled examples: one per deliverable."""
    for line in (out / "raw.jsonl").open():
        job = json.loads(line)
        n = len(job["acceptance"])
        yield {"key": f"{job['id']}-good", "capability": job["capability"], "defect": None, "task": job["task"],
               "acceptance": job["acceptance"], "deliverable": job["good"], "met": [True] * n}
        for k, d in enumerate(job["defective"]):
            yield {"key": f"{job['id']}-d{k}", "capability": job["capability"], "defect": d["defect"],
                   "task": job["task"], "acceptance": job["acceptance"], "deliverable": d["deliverable"],
                   "met": [i != d["breaks_criterion"] for i in range(n)]}


async def judge(model: str, ex: dict) -> list:
    out = await call({"model": model, "max_tokens": 4_000 if model == GEN_MODEL else 8_000,
                      "messages": [{"role": "system", "content": SYSTEM},
                                   {"role": "user", "content": user_msg(ex["task"], ex["acceptance"], ex["deliverable"])}],
                      "response_format": {"type": "json_schema", "json_schema": {"name": "Verdict", "schema": JUDGE_SCHEMA}}})
    by = {c["criterion"]: c for c in out["per_criterion"]}
    return [{"met": bool(by.get(i, {}).get("met")), "reason": str(by.get(i, {}).get("reason") or "")[:300]}
            for i in range(len(ex["acceptance"]))]


async def check(out: Path, concurrency: int) -> None:
    path = out / "checked.jsonl"
    seen = {json.loads(line)["key"] for line in path.open()} if path.exists() else set()
    gate, lock = asyncio.Semaphore(concurrency), asyncio.Lock()

    async def one(ex):
        async with gate:
            try:
                judged = await judge(GEN_MODEL, ex)
            except (json.JSONDecodeError, KeyError, MetaAPIError):
                return
            ex["reasons"] = [j["reason"] for j in judged]
            ex["agree"] = [j["met"] for j in judged] == ex["met"]
            async with lock:
                with path.open("a") as f:
                    f.write(json.dumps(ex) + "\n")

    await asyncio.gather(*(one(ex) for ex in examples(out) if ex["key"] not in seen))
    rows = [json.loads(line) for line in path.open()]
    print(f"checked {len(rows)}, agree {sum(r['agree'] for r in rows)}")


async def gold(out: Path, n: int, concurrency: int) -> None:
    rows = [r for r in map(json.loads, (out / "checked.jsonl").open()) if r["agree"]]
    by_cap = defaultdict(list)
    for r in rows:
        by_cap[r["capability"]].append(r)
    rng = random.Random(7)
    picked = [r for cap in sorted(by_cap) for r in rng.sample(by_cap[cap], min(len(by_cap[cap]), n // len(by_cap)))]
    gate = asyncio.Semaphore(concurrency)

    async def one(ex):
        async with gate:
            try:
                ex["teacher"] = [j["met"] for j in await judge(GOLD_MODEL, ex)]
            except (json.JSONDecodeError, KeyError, MetaAPIError):
                ex["teacher"] = None
            return ex

    done = [g for g in await asyncio.gather(*(one(ex) for ex in picked)) if g["teacher"] is not None]
    (out / "gold.jsonl").write_text("".join(json.dumps(g) + "\n" for g in done))
    agree = sum(g["teacher"] == g["met"] for g in done)
    print(f"gold {len(done)}, teacher agrees with construction on {agree} ({agree / max(len(done), 1):.1%})")


def target(ex: dict) -> str:
    per = [{"criterion": i, "met": m, "reason": ex["reasons"][i]} for i, m in enumerate(ex["met"])]
    return json.dumps({"verdict": "pass" if all(ex["met"]) else "fail", "per_criterion": per, "confidence": 0.9})


def split(out: Path) -> None:
    gold_keys = {json.loads(line)["key"] for line in (out / "gold.jsonl").open()} if (out / "gold.jsonl").exists() else set()
    rows = [r for r in map(json.loads, (out / "checked.jsonl").open()) if r["agree"]]
    jobs = defaultdict(list)  # split by job, so a job's variants never straddle train and test
    for r in rows:
        jobs[(r["capability"], r["key"].split("-")[0])].append(r)
    by_cap = defaultdict(list)
    for (cap, _), group in jobs.items():
        by_cap[cap].append(group)
    rng, parts = random.Random(80), {"train": [], "val": [], "test": []}
    for cap, groups in by_cap.items():
        rng.shuffle(groups)
        # Gold jobs go to test first, so the teacher-checked set is the eval anchor.
        groups.sort(key=lambda g: not any(r["key"] in gold_keys for r in g))
        n = len(groups)
        test, val = groups[: n // 10], groups[n // 10: n // 5]
        parts["test"] += [r for g in test for r in g]
        parts["val"] += [r for g in val for r in g]
        parts["train"] += [r for g in groups[n // 5:] for r in g]
    for name, part in parts.items():
        with (out / f"{name}.jsonl").open("w") as f:
            for r in part:
                f.write(json.dumps({"messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user_msg(r["task"], r["acceptance"], r["deliverable"])},
                    {"role": "assistant", "content": target(r)}],
                    "capability": r["capability"], "defect": r["defect"], "key": r["key"]}) + "\n")
    stats = {name: {"examples": len(p), "pass": sum(all(r["met"]) for r in p),
                    "by_capability": dict(Counter(r["capability"] for r in p)),
                    "by_defect": dict(Counter(r["defect"] or "none" for r in p))} for name, p in parts.items()}
    (out / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("generate", "check", "gold", "split"))
    ap.add_argument("--out", default="../data/den-verifier")
    ap.add_argument("--jobs", type=int, default=2000)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        if a.step == "generate":
            asyncio.run(generate(out, a.jobs, a.concurrency))
        elif a.step == "check":
            asyncio.run(check(out, a.concurrency))
        elif a.step == "gold":
            asyncio.run(gold(out, a.n, a.concurrency))
        else:
            split(out)
    except SpendCapExceeded as e:
        sys.exit(f"stopped by the Meta spend guard: {e}")


if __name__ == "__main__":
    main()
