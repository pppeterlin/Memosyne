---
name: memosyne-augury
description: >-
  Run and read retrieval evaluations — golden-set eval, the Augury Replay
  (real-query regression check), and toggle comparisons. Use when the user
  changes retrieval code, asks "did my change break recall?", wants to
  compare configurations, or needs a baseline before a release. Do NOT use
  for one-off searches — that's memosyne-search.
---

# The Augury — Memosyne Retrieval Eval Protocol

> 神話定位：The Augury — 不是預測，是看見記憶之間是否仍有正確的連繫。Every retrieval
> change is a tremor; the Augury tells you whether the foundations cracked.

This skill governs the safety net around retrieval changes. Two
mechanisms: golden eval (synthetic, fast, repeatable) and replay
(real-usage, slower, drift-detecting).

---

## 0. The Cardinal Rules

- **Capture pollutes weights if not gated.** Replay sets
  `record_access=False` and clears `MEMOSYNE_CAPTURE_QUERIES` during
  execution. Don't bypass these guards.
- **Path-level comparison, not chunk_id.** Replay compares which memories
  surfaced, not internal chunk addresses. This survives v0.6's chunk-id
  schema change.
- **One change at a time.** Toggle one knob, run eval, document the
  delta. Bundled changes hide root cause.

---

## 1. Two evals, two purposes

| Tool | Source | Speed | Use case |
|---|---|---|---|
| `memosyne eval [--sample]` | `golden.yaml` (synthetic) | Fast, deterministic | Pre-merge, CI, regression pinning |
| `memosyne query-log --replay baseline.jsonl` | Real captured queries | Slower, real-world | "Did my change disrupt actual usage?" |

You usually want **both**: golden catches obvious regressions, replay
catches the long tail.

---

## 2. Golden eval

```
memosyne eval --sample          # against sample_vault/_eval/golden.yaml
memosyne eval --golden path/to/your/golden.yaml --top-k 10
```

Reports `Recall@1 / @5 / @10` and `MRR`. Compare against the last
recorded baseline. The v0.2 frozen baseline is `R@1 0.196 / R@5 0.582
/ R@10 0.870 / MRR 0.358` — defend it.

---

## 3. The Augury Replay

### Capture (opt-in)

```
export MEMOSYNE_CAPTURE_QUERIES=1
# normal usage — every search records (query, retrieved_paths, …)
```

Default off; only contributors who consent enable it. PII (email, phone,
long tokens) is scrubbed at write time.

### Export a baseline

```
memosyne query-log --export --since 7d > /tmp/baseline.jsonl
memosyne query-log --stats             # sanity check what's there
```

### Replay against current code

```
memosyne query-log --replay /tmp/baseline.jsonl --top-k 10
```

Three numbers:

- `mean_jaccard@k` — average overlap of retrieved paths. 1.0 = identical;
  0.8+ usually safe; below 0.6 means real drift.
- `top1_stability` — fraction of queries whose #1 result stayed the same.
  Often the most user-visible metric.
- `mean_latency_delta_ms` — current minus captured. Positive = slower.

The "Top N regressions" table shows queries with lowest jaccard — these
are your debugging targets.

---

## 4. Toggle comparison workflow

When measuring a retrieval feature (backlink boost, per-prefix decay,
walk strategy):

```
# 1. Pin baseline with feature OFF
MEMOSYNE_BACKLINK_BOOST=0 memosyne eval --sample > eval_off.json

# 2. Pin treatment with feature ON
MEMOSYNE_BACKLINK_BOOST=1 memosyne eval --sample > eval_on.json

# 3. Diff and document
```

Each toggle deserves an Augury report under `Personal_Brain_DB/_vault/
augury_reports/<feature>.md`. The report should answer: what changed,
which direction, by how much, and at what cost (latency, complexity).

---

## 5. Reading a regression

If replay flags a query with jaccard=0.2:

1. Look at the captured paths vs current paths.
2. Re-run the search interactively with `memosyne search`.
3. Inspect score breakdown — is it BM25, dense, or graph walk that
   dropped the previously-top result?
4. Decide: real regression (revert / fix) or intentional shift
   (acceptable, document it).

---

## 6. When NOT to use

- A single user query went wrong → that's debugging, not eval.
  Use `memosyne search` directly.
- Comparing two unrelated features at once → run them separately.
- Pre-release smoke test → `make verify` covers static invariants;
  eval is for retrieval quality specifically.
