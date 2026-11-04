"""
Den Verifier: QLoRA fine-tune of Muse Glimmer 30B into a deliverable judge (hackathon PRD 5.5).

The GCP A100 quota was denied (2026-10-23), so training runs on a Modal
A100 80GB, like the Eleanor fine-tune. Data comes from
scripts/hackathon/verifier_data.py, pushed to the private dataset
OneFrequency/den-verifier-data; the LoRA adapter goes to
OneFrequency/den-verifier-glimmer-30b-lora.

Run:
    modal run modal_apps/den_verifier.py::train --smoke      # 5 steps on 8 rows, proves the stack
    modal run modal_apps/den_verifier.py::train              # full run, pushes the adapter
"""

import datetime
import json
import os

import modal

# Hackathon spend is capped at $140 total: $120 Meta API (META_SPEND_CAP_USD) plus $20 of GPU here.
GPU_CAP_USD = 20
A100_USD_PER_H = 2.5  # observed: the 2h50m training run cost $7.13
TRAIN_H, EVAL_H = 3, 1.25


def _gpu_budget_check() -> None:
    """Refuse to start if this app's spend so far plus one worst-case run (longest timeout) could pass the cap."""
    from modal.billing import workspace_billing_report

    # Daily rows omit the current day and hourly rows span at most 7 days: whole days, then today by the hour.
    today = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (workspace_billing_report(start=datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc), end=today)
            + workspace_billing_report(start=today, resolution="h"))
    spent = sum(float(r["cost"]) for r in rows if r["description"] == "moltbotden-den-verifier")
    worst = A100_USD_PER_H * max(TRAIN_H, EVAL_H)
    if spent + worst > GPU_CAP_USD:
        raise SystemExit(f"den-verifier GPU budget: spent ${spent:.2f}, a run can cost ${worst:.2f}, cap ${GPU_CAP_USD}")
    print(f"den-verifier GPU budget: spent ${spent:.2f} of ${GPU_CAP_USD}")


if modal.is_local():
    _gpu_budget_check()

app = modal.App("moltbotden-den-verifier")

BASE = "unsloth/Muse-Glimmer-30B-unsloth-bnb-4bit"
DATA_REPO = "OneFrequency/den-verifier-data"
ADAPTER_REPO = "OneFrequency/den-verifier-glimmer-30b-lora"
MAX_SEQ = 4096

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git")
    # Exact pins: unpinned, the resolver backtracked to unsloth 2025.7 with an incompatible trl.
    .uv_pip_install("unsloth==2026.10.9", "unsloth_zoo==2026.10.9", "trl==1.15.0", "transformers==5.17.0",
                    "torch==2.14.1", "peft==0.21.2", "datasets==4.8.5", "huggingface_hub", "hf_transfer")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "HF_HOME": "/cache/hf"})
)
cache = modal.Volume.from_name("den-verifier-cache", create_if_missing=True)
hf_secret = modal.Secret.from_name("hf-onefrequency", required_keys=["HF_TOKEN"])


@app.function(image=image, gpu="A100-80GB", timeout=int(TRAIN_H * 3600), volumes={"/cache": cache}, secrets=[hf_secret])
def train(smoke: bool = False, epochs: float = 2.0) -> dict:
    from unsloth import FastModel  # first: unsloth patches transformers on import
    from unsloth.chat_templates import train_on_responses_only
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer

    model, tokenizer = FastModel.from_pretrained(BASE, max_seq_length=MAX_SEQ, load_in_4bit=True,
                                                 token=os.environ["HF_TOKEN"])
    model = FastModel.get_peft_model(
        model, r=16, lora_alpha=32, lora_dropout=0.0, bias="none", random_state=26,
        finetune_vision_layers=False, finetune_language_layers=True,
        finetune_attention_modules=True, finetune_mlp_modules=True,
        use_gradient_checkpointing="unsloth",
    )
    data = load_dataset(DATA_REPO, token=os.environ["HF_TOKEN"])
    train_ds, val_ds = data["train"], data["validation"]
    if smoke:
        train_ds, val_ds = train_ds.select(range(8)), val_ds.select(range(4))

    def fmt(batch):
        return {"text": [tokenizer.apply_chat_template(m, tokenize=False) for m in batch["messages"]]}

    train_ds = train_ds.map(fmt, batched=True, remove_columns=train_ds.column_names)
    val_ds = val_ds.map(fmt, batched=True, remove_columns=val_ds.column_names)
    trainer = SFTTrainer(
        model=model, tokenizer=tokenizer, train_dataset=train_ds, eval_dataset=val_ds,
        args=SFTConfig(
            dataset_text_field="text", max_seq_length=MAX_SEQ, packing=False,
            per_device_train_batch_size=4, gradient_accumulation_steps=4,
            num_train_epochs=epochs, max_steps=5 if smoke else -1,
            learning_rate=1e-4, warmup_ratio=0.03, lr_scheduler_type="cosine",
            logging_steps=5, eval_strategy="steps", eval_steps=5 if smoke else 100,
            save_strategy="no", bf16=True, optim="adamw_8bit", seed=26, report_to="none",
            output_dir="/cache/run",
        ),
    )
    # Learn the verdict JSON only, not the prompt (turn headers from Glimmer's chat_template.jinja).
    trainer = train_on_responses_only(trainer, instruction_part="<|start|>user<|message|>",
                                      response_part="<|start|>assistant to=user<|message|>")
    stats = trainer.train()
    metrics = {"train_loss": stats.training_loss, **trainer.evaluate()}
    print(json.dumps(metrics))
    if not smoke:
        model.push_to_hub(ADAPTER_REPO, token=os.environ["HF_TOKEN"], private=True)
        tokenizer.push_to_hub(ADAPTER_REPO, token=os.environ["HF_TOKEN"], private=True)
    cache.commit()
    return metrics



@app.function(image=image, gpu="A100-80GB", timeout=int(EVAL_H * 3600), volumes={"/cache": cache}, secrets=[hf_secret])
def evaluate(adapter: bool = True, limit: int = 0, batch: int = 16) -> dict:
    """Score base Glimmer or Den Verifier on the held-out test split (PRD 5.5 eval)."""
    import time

    from unsloth import FastModel
    from datasets import load_dataset

    name = ADAPTER_REPO if adapter else BASE
    model, tokenizer = FastModel.from_pretrained(name, max_seq_length=MAX_SEQ, load_in_4bit=True,
                                                 token=os.environ["HF_TOKEN"])
    FastModel.for_inference(model)
    tokenizer.padding_side = "left"
    test = load_dataset(DATA_REPO, token=os.environ["HF_TOKEN"])["test"]
    if limit:
        test = test.select(range(limit))
    rows, started = [], time.monotonic()
    for i in range(0, len(test), batch):
        chunk = test.select(range(i, min(i + batch, len(test))))
        prompts = [tokenizer.apply_chat_template(m[:2], tokenize=False, add_generation_prompt=True) + " to=user<|message|>"
                   for m in chunk["messages"]]
        enc = tokenizer(text=prompts, return_tensors="pt", padding=True).to("cuda")
        out = model.generate(**enc, max_new_tokens=700, do_sample=False)
        for j, m in enumerate(chunk["messages"]):
            text = tokenizer.decode(out[j][enc["input_ids"].shape[1]:], skip_special_tokens=True)
            rows.append({"key": chunk["key"][j], "capability": chunk["capability"][j],
                         "truth": json.loads(m[2]["content"]), "output": text})
    per_example_s = (time.monotonic() - started) / max(len(rows), 1)
    return {"model": name, "per_example_s": per_example_s, **score(rows), "rows": rows}


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


@app.local_entrypoint()
def eval_report(adapter: bool = True, limit: int = 0, out: str = ""):
    result = evaluate.remote(adapter=adapter, limit=limit)
    rows = result.pop("rows")
    print(json.dumps(result, indent=1))
    if out:
        with open(out, "w") as f:
            json.dump({**result, "rows": rows}, f)


# Serving (PRD 5.5): the contract engine's judge. Same system prompt as training
# (scripts/hackathon/verifier_data.py SYSTEM; keep the two identical).
SYSTEM = (
    "You are Den Verifier. Given a task, its acceptance criteria and a worker's deliverable, decide for each "
    "criterion whether the deliverable meets it, judging only from the deliverable. Answer with JSON: "
    '{"verdict": "pass"|"fail", "per_criterion": [{"criterion": int, "met": bool, "reason": str}], '
    '"confidence": number}. The verdict is pass only if every criterion is met.'
)
token_secret = modal.Secret.from_name("den-verifier-token", required_keys=["DEN_VERIFIER_TOKEN"])


# L40S: the 4-bit 30B needs more than an L4's 22 GB. ponytail: one container, gone after 2 idle minutes, so an idle verifier costs nothing and a busy one
# at most one GPU. The engine falls back to the Spark judge on cold starts (over 20 s).
@app.cls(image=image.uv_pip_install("fastapi[standard]"), gpu=os.environ.get("DEN_VERIFIER_GPU", "L40S"),
         volumes={"/cache": cache}, secrets=[hf_secret, token_secret],
         max_containers=1, scaledown_window=120, timeout=600)
class Verifier:
    @modal.enter()
    def load(self):
        from unsloth import FastModel

        self.model, self.tokenizer = FastModel.from_pretrained(ADAPTER_REPO, max_seq_length=MAX_SEQ, load_in_4bit=True,
                                                               token=os.environ["HF_TOKEN"])
        FastModel.for_inference(self.model)

    @modal.fastapi_endpoint(method="POST")
    def verify(self, body: dict):
        """{token, task, acceptance: [str], deliverable} -> {output, seconds}; the caller parses and decides."""
        import hmac
        import time

        from fastapi import HTTPException

        if not hmac.compare_digest(str(body.get("token", "")), os.environ["DEN_VERIFIER_TOKEN"]):
            raise HTTPException(status_code=401)
        crit = "\n".join(f"{i}. {c}" for i, c in enumerate(body["acceptance"]))
        user = f"Task: {body['task']}\n\nAcceptance criteria:\n{crit}\n\nDeliverable:\n{body['deliverable'][:12_000]}"
        prompt = self.tokenizer.apply_chat_template(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            tokenize=False, add_generation_prompt=True) + " to=user<|message|>"
        started = time.monotonic()
        enc = self.tokenizer(text=[prompt], return_tensors="pt").to("cuda")
        out = self.model.generate(**enc, max_new_tokens=700, do_sample=False)
        text = self.tokenizer.decode(out[0][enc["input_ids"].shape[1]:], skip_special_tokens=True)
        return {"output": text, "seconds": round(time.monotonic() - started, 2)}
