#!/usr/bin/env bash
# Memosyne · Vigil of Invariants — verify orchestrator
#
# 跑所有 invariant 檢查 + 單元測試。
# 退出碼 0 = 全綠；任何一條失敗 → 非 0。
#
# 用法：
#   bash scripts/verify.sh
#   bash scripts/verify.sh --staged   # 把 --staged 透傳給支援的檢查
#
set -uo pipefail

passthrough=""
if [[ "${1:-}" == "--staged" ]]; then
  passthrough="--staged"
fi

failures=()

run() {
  local label="$1"; shift
  echo ""
  echo "════ $label ════"
  if "$@"; then
    echo "    ✓ $label"
  else
    echo "    ✗ $label" >&2
    failures+=("$label")
  fi
}

# 1. 靜態 invariant
run "no-real-paths"           bash scripts/check_no_real_paths.sh $passthrough
run "no-private-names"        bash scripts/check_no_private_names.sh $passthrough
run "sample-vault-synthetic"  bash scripts/check_sample_vault_synthetic.sh

# 2. 單元測試（不需要 kuzu / chromadb）
if command -v python3 >/dev/null 2>&1; then
  # pipefail propagates failure even though tail's exit code is 0
  run "unit-tests" bash -c '
    set -o pipefail
    cd Personal_Brain_DB/00_System && \
    python3 -m unittest discover tests -v 2>&1 | tail -20
  '
else
  echo "  (python3 not found — unit-tests skipped)"
fi

echo ""
echo "════════════════════════════════════════"
if (( ${#failures[@]} == 0 )); then
  echo "🜍 The Vigil holds. All invariants pass."
  exit 0
fi

echo "🜍 The Vigil faltered. Failed checks:" >&2
for f in "${failures[@]}"; do
  echo "    - $f" >&2
done
exit 1
