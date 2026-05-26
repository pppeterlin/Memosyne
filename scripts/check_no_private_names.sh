#!/usr/bin/env bash
# Memosyne · Vigil of Invariants — check_no_private_names
#
# 攔截公開檔中出現的私有禁字（真實人名、地名、私有事件、token 樣式等）。
#
# 禁字清單來源：
#   1. .private_terms          （repo root，必須 gitignored）
#   2. ~/.memosyne/private_terms （個人全域，跨 repo 共用）
#
# 兩個檔都不存在時，script 視為「無禁字設定」，靜默 pass。
# 這讓貢獻者可以在無私有清單的環境下開發，但專案維護者可在本機設置守門。
#
# 檔案格式：每行一個禁字（支援 # 註解）。空白行忽略。
#
# 用法：
#   bash scripts/check_no_private_names.sh
#   bash scripts/check_no_private_names.sh --staged   # 只查 staged diff
#
# 退出碼：
#   0 — 乾淨 或 無禁字清單
#   1 — 命中
#
set -euo pipefail

SCAN_TARGETS=(
  "README.md"
  "README.zh.md"
  "AGENTS.md"
  "CLAUDE.md"
  "docs/"
  "sample_vault/"
)

STAGED_ONLY=0
if [[ "${1:-}" == "--staged" ]]; then
  STAGED_ONLY=1
fi

# 集中所有禁字到一個 grep -F file
terms_file=$(mktemp)
hits_file=$(mktemp)
trap 'rm -f "$terms_file" "$hits_file"' EXIT

load_terms() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  while IFS= read -r line; do
    # strip 註解 / 空白
    term="${line%%#*}"
    term="${term#"${term%%[![:space:]]*}"}"
    term="${term%"${term##*[![:space:]]}"}"
    [[ -z "$term" ]] && continue
    echo "$term" >> "$terms_file"
  done < "$f"
}

load_terms ".private_terms"
load_terms "$HOME/.memosyne/private_terms"

if [[ ! -s "$terms_file" ]]; then
  echo "🜍 check_no_private_names: no .private_terms configured — skipped"
  exit 0
fi

term_count=$(wc -l < "$terms_file" | tr -d ' ')

scan_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  case "$f" in *.local.md) return 0 ;; esac
  # grep -F：固定字串（不解釋 regex 元字元），效能也較高
  if grep -F -n -f "$terms_file" "$f" >> "$hits_file" 2>/dev/null; then
    sed -i.bak "s|^|$f:|" "$hits_file" 2>/dev/null || true
    rm -f "$hits_file.bak"
  fi
}

# 簡化：直接用 grep -r -F -f 對多目標跑一次
if (( STAGED_ONLY )); then
  files=$(git diff --cached --name-only -- "${SCAN_TARGETS[@]}" 2>/dev/null || true)
  if [[ -n "$files" ]]; then
    echo "$files" | while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      case "$f" in *.local.md) continue ;; esac
      [[ -f "$f" ]] || continue
      grep -F -n -H -f "$terms_file" "$f" >> "$hits_file" 2>/dev/null || true
    done
  fi
else
  # 排除 *.local.md
  for target in "${SCAN_TARGETS[@]}"; do
    if [[ -d "$target" ]]; then
      while IFS= read -r f; do
        case "$f" in *.local.md) continue ;; esac
        grep -F -n -H -f "$terms_file" "$f" >> "$hits_file" 2>/dev/null || true
      done < <(find "$target" -type f \( -name "*.md" -o -name "*.txt" -o -name "*.yaml" -o -name "*.toml" \) 2>/dev/null)
    elif [[ -f "$target" ]]; then
      grep -F -n -H -f "$terms_file" "$target" >> "$hits_file" 2>/dev/null || true
    fi
  done
fi

if [[ -s "$hits_file" ]]; then
  violations=$(wc -l < "$hits_file" | tr -d ' ')
  echo "🜍 The Vigil — found $violations private-term hit(s) in public files:" >&2
  echo "" >&2
  cat "$hits_file" >&2
  echo "" >&2
  echo "These terms are in your private allow-list (.private_terms or" >&2
  echo "~/.memosyne/private_terms). Either remove them from the file," >&2
  echo "rewrite to synthetic placeholders, or remove from the term list" >&2
  echo "if they are no longer private." >&2
  exit 1
fi

echo "🜍 check_no_private_names: clean ($term_count private terms checked, 0 hits)"
exit 0
