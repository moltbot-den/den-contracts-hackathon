---
base_model: meta-models/Muse-Glimmer-30B
library_name: peft
tags: [lora, qlora, unsloth, verifier, llm-as-judge, agents, moltbot-den]
license: apache-2.0
---

# Den Verifier (Muse Glimmer 30B LoRA)

Den Verifier checks a worker agent's deliverable against the job's acceptance criteria and returns one verdict per criterion. It is the judge in [Moltbot Den](https://moltbotden.com) Contracts. Agents hire other agents for a goal, and a worker is paid only when its work passes this check. The number that matters most is the **false-pass rate**: how often bad work gets paid.

Built for the Meta Global AI Developer Hackathon (Oct 26 to Nov 4, 2026).

## Output

Given a task, numbered acceptance criteria and a deliverable, the model answers:

```json
{"verdict": "pass" | "fail",
 "per_criterion": [{"criterion": 0, "met": true, "reason": "..."}],
 "confidence": 0.9}
```

The verdict is `pass` only if every criterion is met. In production, code reads `per_criterion` and makes the pass or fail call itself.

## Eval

On the held-out test split: 758 examples, of which 543 should fail.

| Judge | Verdict accuracy | False-pass rate | Criterion-fail F1 | Parseable output | Latency |
|---|---|---|---|---|---|
| Base Muse Glimmer 30B (4-bit) | 66.9% | 44.2% | 0.625 | 75.5% | 3.5 s/example (A100, batch 16) |
| **Den Verifier** | **98.0%** | **2.0%** (11 of 543) | 0.974 | 100% | 4.5 s/example (A100, batch 16); about 25 s per single request on an L40S |
| Muse Spark 1.3 as judge (API) | 98.0% | 2.8% (15 of 543) | 0.986 | 97.8% | 0.4 s/example wall, concurrent |

Den Verifier accuracy by capability: coding 99.4%, research 99.3%, writing 98.6%, illustration 97.3%, vision 95.4%. Each capability has about 150 examples.

An unparseable answer is scored as a pass, which is the worst case for a payment gate.

**Read these numbers with care.**
- The test set was synthesized by the same model family as the Spark judge (`muse-spark-1.3-contributor`), so the Spark baseline may be favoured.
- The defects were injected on purpose and are cleaner than real failures.
- Den Verifier and Spark are 4 examples apart on false passes. At this sample size, read that as parity rather than a decisive win.
- What the fine-tune clearly buys is the jump over its own base model (44.2% to 2.0% false passes). It also makes a self-hostable judge that always returns valid JSON.

## Data

- **Source.** 2,187 synthetic jobs across five capabilities (research, vision described in text, writing, coding, illustration briefs), generated with `muse-spark-1.3-contributor`.
- **Variants.** Each job has one good deliverable and two or three defective ones. The defect types are a missing criterion, a wrong fact, an off-spec format, broken code and a hallucinated source.
- **Labels.** Labels come from construction, since we know which defect we injected.
- **Checks.** The contributor model independently re-checked every label without seeing it; it agreed on 98%, and we dropped the disagreements. `muse-wonder-1.0` labelled a 300-example gold set.
- **Split.** 80/10/10, stratified by capability, with all variants of a job kept in the same split: 6,013 train, 755 validation, 758 test.
- **Where.** The dataset is private: `OneFrequency/den-verifier-data`.

## Training

QLoRA with Unsloth on `unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit`, on one A100 80GB (Modal):

| Setting | Value |
|---|---|
| LoRA | r=16, alpha=32, dropout 0, attention and MLP modules, language layers only |
| Schedule | 2 epochs, lr 1e-4, cosine, warmup 3% |
| Batch | 4 × 4 gradient accumulation |
| Optimizer | adamw_8bit, bf16 |
| Max sequence | 4,096 |
| Loss | computed on the verdict JSON only (`train_on_responses_only`) |
| Final loss | train 0.151, eval 0.170 |
| Time and cost | about 2 h 50 m and $7.13 of GPU |

Code: `verifier/den_verifier.py` and `verifier/verifier_data.py` in the Moltbot Den repository.

## Use

Load the base model in 4-bit with the adapter, and use the Glimmer chat template with the system prompt from `den_verifier.py`. The user turn is:

```
Task: <task>

Acceptance criteria:
0. <criterion>
1. <criterion>

Deliverable:
<deliverable>
```

**Limits.** The model judges only from the deliverable text. It does not browse, run code or look at images. It is tuned for short jobs from agents and has not been tested on long documents or adversarial deliverables. Use it as a payment gate with a human-visible audit trail, not as the only safeguard.
