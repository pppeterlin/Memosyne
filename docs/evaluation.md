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

## Eternal Mirror

Eternal Mirror samples questions from `hyqe_cache.json` and checks whether retrieval returns the source memory path.

Run from `Personal_Brain_DB/00_System`:

```bash
cd Personal_Brain_DB/00_System
python benchmark/retrieval_eval.py --config baseline --n 500 --diff
python benchmark/retrieval_eval.py --config full --n 500 --hygiene --diff
```

Common flags:

| Flag | Purpose |
|---|---|
| `--config baseline` | no muse routing; reference baseline |
| `--config full` | current full retrieval profile |
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
cd Personal_Brain_DB/00_System
make eval-ci
```

## Augury Golden Set

Create a private golden set from the synthetic template:

```bash
cd Personal_Brain_DB/00_System
cp benchmark/golden_set.example.yaml benchmark/golden_set.yaml
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
python benchmark/retrieval_eval.py \
  --config full \
  --hygiene \
  --golden-set golden_set.yaml \
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
cd Personal_Brain_DB/00_System
python benchmark/retrieval_eval.py \
  --config full \
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
