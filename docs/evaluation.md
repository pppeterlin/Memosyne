# Memosyne Evaluation

Memosyne uses two complementary evaluation paths:

- **Eternal Mirror**: self-supervised round-trip checks from HyQE questions back to their source memory.
- **Augury**: a small human-curated golden set for realistic user intent.

Evaluation exists to catch retrieval regressions before merging changes. It should not expose private memories, queries, or summaries in the public repo.

## Privacy Rules

Public repo may include:

- synthetic golden set templates
- aggregate metrics
- documentation with generic examples
- report structure descriptions

Public repo must not include:

- private benchmark queries
- private memory titles, summaries, or snippets
- real relationship/location/event combinations from the vault
- raw reports containing private paths or private query text

Keep private `golden_set.yaml` and private reports in ignored/private locations.

## Ablation (v1.5)

Before 1.5 every query ran through nine stages. To decide what the default
should be, each stage was switched on in turn against one real personal
vault (≈200 journal entries, ≈450 AI chat transcripts, ≈37k chunks,
`BAAI/bge-m3` embeddings, mixed Chinese / English). Questions come from the
Eternal Mirror pool (one LLM-written question per chunk; a hit means the
source memory is returned), in hygiene mode, with no access recording.

| Search path | Journal R@1 | R@5 | MRR | AI chats R@1 | R@5 | MRR |
|---|---:|---:|---:|---:|---:|---:|
| dense only | 0.46 | 0.67 | 0.55 | 0.67 | 0.83 | 0.74 |
| BM25 only *(core install)* | 0.57 | 0.72 | 0.63 | 0.66 | 0.87 | 0.75 |
| **dense + BM25 → RRF** *(1.5 default)* | **0.57** | **0.79** | **0.65** | **0.68** | **0.84** | **0.75** |
| + temporal rerank | 0.54 | 0.78 | 0.63 | 0.68 | 0.84 | 0.75 |
| + graph search | 0.38 | 0.68 | 0.52 | 0.41 | 0.82 | 0.57 |
| + PPR spreading activation | 0.35 | 0.65 | 0.49 | 0.39 | 0.81 | 0.55 |
| + backlink boost | 0.30 | 0.62 | 0.45 | 0.26 | 0.77 | 0.45 |
| + ACT-R rerank | 0.28 | 0.61 | 0.43 | 0.25 | 0.73 | 0.43 |
| + domain routing *(1.0 default)* | 0.26 | 0.60 | 0.42 | 0.21 | 0.74 | 0.41 |

Journal n = 125, AI chats n = 100; each row adds one stage to the row above.
Recall@10 stays within 0.76–0.83 (journal) and 0.84–0.91 (chats) for every
row: the extra stages rarely lose the right memory, they push it down the
list. Per-query latency of the 1.0 default was about 6× the 1.5 default on
the same machine.

Search scope, using the 1.5 default path:

| Questions about | All memories R@5 | Life memories only R@5 |
|---|---:|---:|
| Journal entries (n = 125) | 0.79 | 0.79 |
| Profile (n = 25) | 0.68 | 0.88 |

Leaving AI chat transcripts out of scope costs nothing for journal
questions and helps profile questions, so 1.5 searches life memories by
default and chats on request.

### What this does and does not show

- It shows that for *direct lookups* — the question names what the memory
  is about — fusing dense and keyword results is the best ranking measured,
  and each further stage makes top-1 accuracy worse.
- It does **not** measure what the graph and ACT-R stages were built for:
  multi-hop questions whose answer shares no words with the query, and
  recency. Generated questions are lexically close to their source, which
  favours dense and BM25. Those stages are therefore kept, as opt-in
  `memosyne.labs` features (`MEMOSYNE_SEARCH_PROFILE=full`), rather than
  deleted.
- Graph search ranked tied memories in arbitrary order before 1.5, so every
  row that includes it varies from run to run (5 of 14 repeated queries
  changed). That noise is part of why it hurts top-1 accuracy, and the gap
  to the default path is far larger than the variation — but the exact
  figures for those rows are single runs, not averages.
- ACT-R depends on the vault's access history; on this vault that history
  was mostly earlier evaluation runs, which is a weak signal.
- The temporal rerank only acts when a query contains a time expression;
  its small negative effect here (3 questions) is within noise, and it
  stays in the default path.
- One vault, one embedding model. Rerun it on yours before trusting it:

```bash
make eval        # core profile, N=500, hygiene
make eval-full   # full labs stack on the same sample
```

## Eternal Mirror

Eternal Mirror samples questions from `hyqe_cache.json` and checks whether retrieval returns the source memory path.

```bash
python -m memosyne.labs.retrieval_eval --config core --n 500 --hygiene --diff
python -m memosyne.labs.retrieval_eval --config full --n 500 --hygiene --diff
```

Common flags:

| Flag | Purpose |
|---|---|
| `--config core` | the default search path: dense + BM25 fusion (`baseline` is an alias) |
| `--config full` | the pre-1.5 stack: graph, PPR, backlink, ACT-R, domain routing |
| `--config labs` | the labs stages without domain routing |
| `--config boost` | historical boost comparison |
| `--config hard` | hard muse filtering comparison |
| `--n 500` | sample count |
| `--top-k 10` | retrieval cutoff |
| `--seed 42` | reproducible sample seed |
| `--hygiene` | exclude HyQE view to reduce self-hit leakage |
| `--stratify-by muse` | stratify by Muse domain |
| `--stratify-by length` | stratify by question length |
| `--stratify-by both` | stratify by both dimensions |
| `--diff` | compare with previous report for the same config |
| `--fail-on-regression` | exit non-zero if regression threshold is exceeded |

CI-style check:

```bash
make eval-ci
```

## Augury Golden Set

Create a private golden set from the synthetic template:

```bash
cp docs/golden_set.example.yaml ~/private/golden_set.yaml   # keep it out of the repo
```

Example shape:

```yaml
Clio:
  - query: "sample journal 的主要情緒是什麼"
    expected_paths:
      - "30_Journal/2026/sample_journal.md"
    tags: [synthetic, journal]
    notes: "測試日記情緒召回"
```

Run with Augury metrics:

```bash
python -m memosyne.labs.retrieval_eval \
  --config core \
  --hygiene \
  --golden-set ~/private/golden_set.yaml \
  --diff
```

## Reports

Reports are written under benchmark reports paths managed by the private vault/artifact strategy. Treat report contents as private unless they were generated entirely from synthetic data.

Each report can include:

- Recall@1
- Recall@5
- Recall@10
- MRR
- per-Muse breakdown
- per-length breakdown
- Augury metrics when a golden set is provided
- diff against the previous report for the same config

## Release Validation

Before merging retrieval-sensitive changes:

```bash
python -m memosyne.labs.retrieval_eval \
  --config core \
  --n 500 \
  --hygiene \
  --stratify-by both \
  --diff
```

For v0.3 operational-only changes, at minimum run:

```bash
memosyne health
memosyne search "測試查詢" --no-record-access
memosyne mcp --check
memosyne chronicle --stats
```

Use `--no-record-access` for smoke-test queries so validation does not alter
Chronicle state or ACT-R reranking inputs before an Eternal Mirror comparison.

If retrieval code changed, also run Eternal Mirror. If user-facing retrieval behavior changed, run Augury with a private golden set.

## Interpreting Regressions

- Small metric movement can come from sample composition; rerun with the same seed before acting.
- Treat Recall@10 drops as more serious than top-1 tuning noise.
- A Muse routing change may improve top-5 experience while reducing broad recall; document the tradeoff.
- Do not tune against private benchmark queries in public commits.

## Hygiene Mode

Use `--hygiene` when comparing retrieval quality. It excludes HyQE view chunks during evaluation so the system does not win by directly matching generated questions against themselves.

## The Augury Replay (v0.5+)

Golden eval pins synthetic regressions. Replay pins **real-usage** regressions
by comparing what the live vault retrieves now against what it retrieved
when the queries were originally captured.

### Capture (opt-in)

```bash
export MEMOSYNE_CAPTURE_QUERIES=1
# normal usage records (query, retrieved_paths, top_k, latency, source)
# into <artifacts>/query_log.jsonl
# PII (email / phone / long tokens / user-supplied terms) is scrubbed at
# write time; nothing leaves the local machine.
```

`MEMOSYNE_QUERY_SCRUB_TERMS=name1,name2,...` adds custom redaction terms.

### Export → Replay

```bash
memosyne query-log --export --since 7d > /tmp/baseline.jsonl
memosyne query-log --replay /tmp/baseline.jsonl --top-k 10
```

Three drift metrics, all **path-level** (chunk_id schema changes do not
distort comparisons):

| Metric | What it tells you |
|---|---|
| `mean_jaccard@k`       | average overlap of retrieved memory paths |
| `top1_stability`       | fraction of queries whose #1 result stayed identical |
| `mean_latency_delta_ms`| current minus captured search latency |

Side effects are suppressed during replay (no ACT-R writes, no recursive
capture) so the act of measuring does not shift what's being measured.

The "Top N regressions" table surfaces queries with the lowest jaccard —
those are the debugging targets when a refactor moves the metric.

### Toggle comparison

`scripts/run_toggle_reports.sh` runs `memosyne eval --sample` under
several configurations (`MEMOSYNE_WALK`, `MEMOSYNE_BACKLINK_COEF`) and
writes results under `sample_vault/_eval/toggle_reports/`. Methodology
proof on the public sample; private vault toggle reports stay local.

For meaningful effect-size differentiation, run the same matrix against
your private golden set or replay captured queries — the sample golden's
N=5 sits below the noise floor for subtle multiplicative bonuses.
