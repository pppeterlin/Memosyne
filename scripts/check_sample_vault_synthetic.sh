#!/usr/bin/env bash
# Memosyne · Vigil of Invariants — check_sample_vault_synthetic
#
# 確保 sample_vault/ 是完全合成的：
#   1. 不含任何真實 host path
#   2. 不含 .env / *secret* / *.key 檔案
#   3. enrichment frontmatter（如有）中的 entities 應在白名單中
#
# 白名單檔案：sample_vault/_synthetic_entities.txt
# 若白名單不存在，跳過第 3 條檢查（用於早期 sample 建構期）。
#
# 用法：
#   bash scripts/check_sample_vault_synthetic.sh
#
set -euo pipefail

SAMPLE_DIR="sample_vault"

if [[ ! -d "$SAMPLE_DIR" ]]; then
  echo "🜍 check_sample_vault_synthetic: no $SAMPLE_DIR directory — skipped"
  exit 0
fi

violations=0

# 1. 不含真實 host path
if grep -rEn '(/Users/[a-zA-Z0-9_-]+/|/home/[a-zA-Z0-9_-]+/)' "$SAMPLE_DIR" \
   --include="*.md" --include="*.txt" --include="*.yaml" --include="*.toml" \
   2>/dev/null; then
  echo "🜍 sample_vault 含真實 host path" >&2
  violations=$((violations + 1))
fi

# 2. 不含敏感檔
sensitive=$(find "$SAMPLE_DIR" -type f \
  \( -name ".env" -o -name "*.key" -o -name "*secret*" -o -name "*.pem" \) \
  2>/dev/null)
if [[ -n "$sensitive" ]]; then
  echo "🜍 sample_vault 含可疑敏感檔：" >&2
  echo "$sensitive" >&2
  violations=$((violations + 1))
fi

# 3. 白名單比對（可選）
whitelist="$SAMPLE_DIR/_synthetic_entities.txt"
if [[ -f "$whitelist" ]]; then
  # 用 link_extractor 抽 entity，比對是否都在白名單
  if command -v python3 >/dev/null 2>&1; then
    extracted=$(python3 - <<EOF 2>/dev/null || true
import sys, json
from pathlib import Path
sys.path.insert(0, "Personal_Brain_DB/00_System")
try:
    from link_extractor import extract_from_vault
    res = extract_from_vault(Path("$SAMPLE_DIR"))
    ents = set()
    for ext in res.values():
        for field in ("people", "locations", "events"):
            for e in ext.get("entities", {}).get(field, []):
                ents.add(e)
        if ext.get("period"):
            ents.add(ext["period"])
    print("\n".join(sorted(ents)))
except Exception:
    pass
EOF
)
    if [[ -n "$extracted" ]]; then
      not_listed=$(echo "$extracted" | grep -F -v -f "$whitelist" || true)
      if [[ -n "$not_listed" ]]; then
        echo "🜍 sample_vault 出現未在白名單的 entity：" >&2
        echo "$not_listed" >&2
        echo "" >&2
        echo "若這些是合成名稱，把它們加進 $whitelist；" >&2
        echo "若是不小心混入的真實名稱，請改寫到不可回推的合成形式。" >&2
        violations=$((violations + 1))
      fi
    fi
  fi
else
  echo "  (no $whitelist — entity whitelist check skipped)"
fi

if (( violations > 0 )); then
  exit 1
fi

echo "🜍 check_sample_vault_synthetic: clean"
exit 0
