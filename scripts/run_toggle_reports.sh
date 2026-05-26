#!/usr/bin/env bash
# Memosyne · WS3 toggle ablation against sample golden set.
#
# Holds two knobs at default, flips the third, writes JSON-ish output
# under sample_vault/_eval/toggle_reports/<knob>__<setting>.txt.
# Compares against baseline.txt (all defaults).
#
# Caller may set MEMOSYNE_VAULT_DIR=sample_vault before running, or
# rely on `memosyne eval --sample` to auto-set artifact env.
#
# Note: decay toggle is documented but not flipped here — the
# per-prefix decay map is a structured config, not a boolean. The
# existing decay map already defines the "on" state; "off" would need
# an empty map and is exercised in the unit tests for mneme_weight.

set -uo pipefail

OUT_DIR="sample_vault/_eval/toggle_reports"
mkdir -p "$OUT_DIR"

run_eval() {
  local label="$1"; shift
  local out="$OUT_DIR/${label}.txt"
  echo "── $label ──"
  env "$@" python memosyne.py eval --sample 2>&1 | tee "$out" | tail -8
  echo ""
}

# 1. Baseline — everything default
run_eval "baseline"

# 2. Walk strategy toggle
run_eval "walk__off"   MEMOSYNE_WALK=off
run_eval "walk__fast"  MEMOSYNE_WALK=fast

# 3. Backlink boost toggle
run_eval "backlink__off"  MEMOSYNE_BACKLINK_COEF=0

echo "════════════════════════════════════════"
echo "Reports written to $OUT_DIR/"
echo ""
echo "Summary (Recall@5 / Recall@10 / MRR):"
for f in "$OUT_DIR"/*.txt; do
  name=$(basename "$f" .txt)
  r5=$(grep -E "Recall@5" "$f"  | head -1 | awk '{print $NF}')
  r10=$(grep -E "Recall@10" "$f" | head -1 | awk '{print $NF}')
  mrr=$(grep -E "MRR"      "$f" | head -1 | awk '{print $NF}')
  printf "  %-20s R@5=%s  R@10=%s  MRR=%s\n" "$name" "$r5" "$r10" "$mrr"
done
