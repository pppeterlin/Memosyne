#!/usr/bin/env bash
# Memosyne · Vigil of Invariants — verify orchestrator
#
# Runs every static invariant plus the unit tests. Exit code 0 = all green.
# The unit tests need only the core install (`pip install -e ".[dev]"`).
#
#   bash scripts/verify.sh
#   bash scripts/verify.sh --staged   # path checks look at the staged diff only
#
set -uo pipefail

passthrough=""
if [[ "${1:-}" == "--staged" ]]; then
  passthrough="--staged"
fi

PYTHON="${PYTHON:-python3}"
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

# 1. Static invariants
run "no-real-paths"           bash scripts/check_no_real_paths.sh $passthrough
run "no-private-names"        bash scripts/check_no_private_names.sh $passthrough
run "sample-vault-synthetic"  bash scripts/check_sample_vault_synthetic.sh

# 2. Unit tests — tests/conftest.py points Memosyne at an empty temporary
#    home, so they can never touch a real vault.
run "unit-tests" "$PYTHON" -m pytest tests/unit -q -p no:cacheprovider

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
