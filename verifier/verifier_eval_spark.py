"""Spark as judge on the Den Verifier test split, scored like modal_apps/den_verifier.py (run from moltbotden-api/: python ../scripts/hackathon/verifier_eval_spark.py <test.jsonl>)."""
import asyncio, json, sys, time
sys.path.insert(0, ".")
from services.meta_client import get_meta_client

def score(rows: list) -> dict:
    """Verdict accuracy, per-criterion F1 (positive class: criterion NOT met), and false-pass rate."""
    correct = parsed = false_pass = bad_total = tp = fp = fn = 0
    for r in rows:
        truth = r["truth"]
        try:
            pred = json.loads(r["output"][r["output"].index("{"): r["output"].rindex("}") + 1])
            parsed += 1
        except (ValueError, KeyError):
            pred = {"verdict": "pass", "per_criterion": []}  # unparseable counts as the worst case: a pass
        correct += pred.get("verdict") == truth["verdict"]
        if truth["verdict"] == "fail":
            bad_total += 1
            false_pass += pred.get("verdict") == "pass"
        got = {c.get("criterion"): bool(c.get("met")) for c in pred.get("per_criterion") or [] if isinstance(c, dict)}
        for c in truth["per_criterion"]:
            miss_true, miss_pred = not c["met"], not got.get(c["criterion"], True)
            tp += miss_true and miss_pred
            fp += miss_pred and not miss_true
            fn += miss_true and not miss_pred
    n = max(len(rows), 1)
    f1 = 2 * tp / max(2 * tp + fp + fn, 1)
    return {"n": len(rows), "parsed": parsed / n, "verdict_accuracy": correct / n,
            "criterion_fail_f1": f1, "false_pass_rate": false_pass / max(bad_total, 1)}


SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["pass", "fail"]},
          "per_criterion": {"type": "array", "items": {"type": "object", "properties": {
              "criterion": {"type": "integer"}, "met": {"type": "boolean"}, "reason": {"type": "string"}},
              "required": ["criterion", "met", "reason"], "additionalProperties": False}},
          "confidence": {"type": "number"}}, "required": ["verdict", "per_criterion", "confidence"],
          "additionalProperties": False}

async def main(path):
    rows, gate, t0 = [], asyncio.Semaphore(20), time.monotonic()
    test = [json.loads(l) for l in open(path)]
    async def one(ex):
        async with gate:
            try:
                r = await get_meta_client().create_chat_completion({"model": "muse-spark-1.3", "max_tokens": 3000,
                    "messages": ex["messages"][:2],
                    "response_format": {"type": "json_schema", "json_schema": {"name": "Verdict", "schema": SCHEMA}}})
                out = r["choices"][0]["message"]["content"] or ""
            except Exception as e:
                out = f"error: {e}"
            rows.append({"key": ex["key"], "capability": ex["capability"],
                         "truth": json.loads(ex["messages"][2]["content"]), "output": out})
    await asyncio.gather(*(one(ex) for ex in test))
    res = {"model": "muse-spark-1.3", "per_example_s_wall": (time.monotonic() - t0) / len(rows), **score(rows)}
    print(json.dumps(res, indent=1))
    json.dump({**res, "rows": rows}, open(path.replace("test.jsonl", "eval_spark.json"), "w"))

asyncio.run(main(sys.argv[1]))  # sys.argv[2] ignored (kept for older invocations)
