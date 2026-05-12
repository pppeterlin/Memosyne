#!/usr/bin/env bash
# Memosyne · Vigil of Invariants — check_no_real_paths
#
# 攔截公開文檔中殘留的本機絕對路徑（/Users/<name>/、/home/<name>/、/root/）。
# 公開檔案應該用相對路徑或佔位符（例如 ~/Documents/your-vault）。
#
# 例外：
#   * docs/*.md fenced code block 中標註 `# example` 的行
#   * AGENTS.local.md / *.local.md（gitignored，個人 override）
#
# 退出碼：
#   0 — 乾淨
#   1 — 命中需修復的路徑
#
# 用法：
#   bash scripts/check_no_real_paths.sh
#   bash scripts/check_no_real_paths.sh --staged   # 只檢查 staged diff
#
set -euo pipefail

SCAN_TARGETS=(
  "README.md"
  "README.zh.md"
  "AGENTS.md"
  "CLAUDE.md"
  "docs/"
)

STAGED_ONLY=0
if [[ "${1:-}" == "--staged" ]]; then
  STAGED_ONLY=1
fi

# 真實 path pattern：明確的個人 home 目錄
PATTERN='(/Users/[a-zA-Z0-9_-]+/|/home/[a-zA-Z0-9_-]+/|/root/[a-zA-Z0-9_-]+)'

# 允許的 placeholder（公開示例可用）
PLACEHOLDERS='(/Users/<[^>]+>|/Users/your-|/home/<[^>]+>|/home/your-)'

# 行尾 marker，允許保留
ALLOW_MARK='# *(example|placeholder|public-safe)'

violations=0
hits_file=$(mktemp)
trap 'rm -f "$hits_file"' EXIT

scan_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  # 跳過 *.local.md（gitignored）
  case "$f" in *.local.md) return 0 ;; esac

  while IFS= read -r line; do
    # 跳過明顯的 placeholder 行
    if echo "$line" | grep -Eq "$PLACEHOLDERS"; then continue; fi
    # 跳過標 example 的行
    if echo "$line" | grep -Eq "$ALLOW_MARK"; then continue; fi
    # 命中真實 path
    if echo "$line" | grep -Eq "$PATTERN"; then
      echo "$f: $line" >> "$hits_file"
      violations=$((violations + 1))
    fi
  done < "$f"
}

if (( STAGED_ONLY )); then
  # 只看 staged 變更檔
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    scan_file "$f"
  done < <(git diff --cached --name-only -- "${SCAN_TARGETS[@]}" 2>/dev/null || true)
else
  for target in "${SCAN_TARGETS[@]}"; do
    if [[ -d "$target" ]]; then
      while IFS= read -r f; do
        scan_file "$f"
      done < <(find "$target" -type f \( -name "*.md" -o -name "*.txt" \) 2>/dev/null)
    elif [[ -f "$target" ]]; then
      scan_file "$target"
    fi
  done
fi

if (( violations > 0 )); then
  echo "🜍 The Vigil — found $violations real-looking path(s) in public docs:" >&2
  echo "" >&2
  cat "$hits_file" >&2
  echo "" >&2
  echo "If these are legitimate examples, mark the line with '# example' or" >&2
  echo "use a placeholder like /Users/<your-name>/ to silence the check." >&2
  exit 1
fi

echo "🜍 check_no_real_paths: clean (no host paths leaked)"
exit 0
