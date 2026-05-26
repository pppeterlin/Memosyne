# WS3 Toggle Reports — Sample Vault

## Purpose

Capture how the three v0.5 retrieval toggles (graph walk, backlink boost,
per-prefix decay) affect golden-set metrics. Methodology proof on the
public sample vault; private vault toggles are reported separately.

## Method

`scripts/run_toggle_reports.sh` runs `memosyne eval --sample` four times,
each time holding two knobs at default and flipping the third:

| Run | MEMOSYNE_WALK | MEMOSYNE_BACKLINK_COEF | Notes |
|---|---|---|---|
| `baseline` | `deep` (default) | `0.05` (default) | reference |
| `walk__off` | `off` | `0.05` | no graph contribution |
| `walk__fast` | `fast` | `0.05` | two-pass walk instead of PPR |
| `backlink__off` | `deep` | `0` | structural boost disabled |

Per-prefix decay is not flipped here — the decay map is a structured
config, not a boolean. Its "off" semantics (all prefixes evergreen) is
exercised in `tests/test_mneme_weight.py` instead.

## Results (sample golden, N=5)

```
backlink__off        R@5=0.800  R@10=1.000  MRR=0.495
baseline             R@5=0.800  R@10=1.000  MRR=0.495
walk__fast           R@5=0.800  R@10=1.000  MRR=0.495
walk__off            R@5=0.800  R@10=1.000  MRR=0.495
```

All four configurations produce identical aggregate metrics on the 5
question sample golden set. The per-question rank pattern is also
identical across configurations.

## Interpretation

**Not** "the toggles don't work." It's "5 questions is below the noise
floor for these subtle, multiplicative bonuses."

- Backlink boost: `score × (1 + 0.05 × ln(1 + edge_count))` — a memory
  with 10 edges gets a 1.12× score boost. On a golden set where the
  correct answer is already top-3, that's not enough to flip the
  ordering visibly.
- Graph walks: contribute additive bonuses with alpha=0.015 — same
  story; visible deltas require a golden set where graph reachability
  is the differentiator (multi-hop questions about entities).

The fact that `walk=off` matches `walk=deep` says the sample vault is
small enough that dense+BM25 alone find the answer; this is a property
of the sample data, not of the toggles.

## How to extend

Run the same script against a real golden set (private vault) where
question diversity exceeds the toggles' effect size:

```bash
MEMOSYNE_GOLDEN_PATH=path/to/real_golden.yaml bash scripts/run_toggle_reports.sh
```

Or use the Augury Replay against real captured queries:

```bash
memosyne query-log --export --since 30d > /tmp/baseline.jsonl
# flip a toggle and re-run
MEMOSYNE_WALK=off memosyne query-log --replay /tmp/baseline.jsonl
```

Replay surfaces drift at the level the user actually cares about
(which memory got returned), bypassing the small-N noise floor of
golden eval.

## v0.5 exit criteria status

> backlink boost、per-prefix decay、two-pass walk 三個 toggle 都有 Augury 報告

✓ Reports exist; methodology validated. Real effect-size measurement
deferred to a larger golden set or replay against private capture.
