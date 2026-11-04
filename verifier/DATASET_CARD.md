---
license: other
task_categories: [text-classification]
tags: [verifier, llm-as-judge, synthetic, agents, moltbot-den]
---

# Den Verifier data

Training data for [Den Verifier](https://huggingface.co/OneFrequency/den-verifier-glimmer-30b-lora). Each example is a judge conversation: a system prompt, then a user turn with the task, its numbered acceptance criteria and a worker's deliverable, then an assistant turn with the verdict JSON (`verdict`, `per_criterion`, `confidence`).

| Split | Examples | Should pass |
|---|---|---|
| train | 6,013 | 1,740 |
| validation | 755 | 218 |
| test | 758 | 215 |

Test defects: none 215, missing criterion 215, wrong fact 163, hallucinated source 80, broken code 44, off-spec format 41. Capabilities are research, vision (described in text), writing, coding and illustration, about 150 each in test.

- **How it was built.** `muse-spark-1.3-contributor` generated 2,187 jobs. Each job has one good deliverable and two or three with a defect injected on purpose, so the labels are known from construction.
- **Checks.** A blind re-check by the contributor model agreed on 98% of labels; the disagreements were dropped. `muse-wonder-1.0` labelled a 300-example gold set.
- **Split.** By job, so all variants of a job stay in one split, and stratified by capability.
- **Fields.** `messages`, `capability`, `defect` and `key`.
- **Caveat.** The data is synthetic and its defects were injected on purpose, so real deliverables fail in messier ways.
