#!/usr/bin/env python3
"""
Phase 4.4 Backfill — chat_category for existing 20_AI_Chats records

僅對 20_AI_Chats/ 下的 .md 檔案呼叫 LLM 做單一輪分類，把 chat_category 欄位
寫入 frontmatter（不動其他 enrichment）。

分類：personal / knowledge / mixed / ""（判斷失敗）

用法：
  python3 backfill_chat_category.py                 # dry-run 全部
  python3 backfill_chat_category.py --apply         # 實際寫入
  python3 backfill_chat_category.py --limit 5       # 只處理前 5 個
  python3 backfill_chat_category.py --model <name>  # 指定模型
"""
import argparse
import json
import re
import sys
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent
BASE       = SYSTEM_DIR.parent
sys.path.insert(0, str(SYSTEM_DIR))

from enrich import parse_frontmatter  # noqa: E402
from llm_client import chat_text      # noqa: E402

CHAT_DIR = BASE / "20_AI_Chats"

CATEGORIZE_PROMPT = """\
你是分類助手。根據下方 AI 對話記錄，判斷對話類別：

- "personal"  = 圍繞作者的生活、情緒、決策、人際
- "knowledge" = 純客觀/技術問答（如「X 怎麼運作」「Y 是什麼」），無個人利害關係
- "mixed"     = 個人情境但主要在請教一般知識

只回傳一個 JSON 物件，不要任何額外說明：
{{"chat_category": "personal|knowledge|mixed"}}

對話標題：{title}
對話片段（前 2000 字）：
{excerpt}
"""


def classify(title: str, text: str, model: str) -> str:
    excerpt = text[:2000]
    prompt  = CATEGORIZE_PROMPT.format(title=title, excerpt=excerpt)
    raw = chat_text(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        think=False,
    ).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return ""
    try:
        cat = json.loads(raw[start:end + 1]).get("chat_category", "").strip().lower()
    except json.JSONDecodeError:
        return ""
    return cat if cat in {"personal", "knowledge", "mixed"} else ""


def write_category(path: Path, category: str) -> bool:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end < 0:
        return False
    fm_block = content[3:end]
    after    = content[end + 4:]

    # 若已有 chat_category 欄位則更新；否則追加
    new_line = f'chat_category: "{category}"'
    if re.search(r'^chat_category:\s*.*$', fm_block, re.MULTILINE):
        fm_block = re.sub(r'^chat_category:\s*.*$', new_line, fm_block, flags=re.MULTILINE)
    else:
        # 加在 hyqe_questions 那行後面，或 frontmatter 末尾
        if 'hyqe_questions:' in fm_block:
            fm_block = re.sub(r'(hyqe_questions:\s*\[\])', r'\1\n' + new_line, fm_block)
        else:
            fm_block = fm_block.rstrip() + "\n" + new_line

    path.write_text(f"---{fm_block}\n---{after[3:] if after.startswith('---') else after}",
                    encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="實際寫入（預設 dry-run）")
    ap.add_argument("--limit", type=int, default=0, help="只處理前 N 個")
    ap.add_argument("--model", default="proxy:claude-haiku-4-5", help="LLM 模型")
    ap.add_argument("--force", action="store_true", help="覆蓋已有 chat_category")
    args = ap.parse_args()

    if not CHAT_DIR.exists():
        print(f"找不到 {CHAT_DIR}")
        return

    files = sorted(CHAT_DIR.rglob("*.md"))
    if args.limit:
        files = files[:args.limit]

    total = len(files)
    done = skip = err = 0
    counts = {"personal": 0, "knowledge": 0, "mixed": 0, "": 0}

    for i, path in enumerate(files, 1):
        content = path.read_text(encoding="utf-8")
        _, fm, body = parse_frontmatter(content)
        existing = str(fm.get("chat_category", "")).strip('"').strip("'").lower()
        if existing and not args.force:
            skip += 1
            continue

        title = fm.get("title", path.stem)
        text  = f"{title}\n{body}"

        print(f"[{i}/{total}] {path.relative_to(BASE)} ... ", end="", flush=True)
        try:
            cat = classify(title, text, args.model)
        except Exception as e:
            print(f"ERROR: {e}")
            err += 1
            continue

        counts[cat] = counts.get(cat, 0) + 1
        print(f"{cat or '<empty>'}", end="")

        if args.apply:
            if write_category(path, cat):
                print(" ✓")
            else:
                print(" (write failed)")
                err += 1
                continue
        else:
            print(" [dry-run]")
        done += 1

    print(f"\n分類分布：{counts}")
    print(f"完成 {done}｜跳過 {skip}｜錯誤 {err}（共 {total}）")
    if not args.apply:
        print("⚠️  dry-run 模式，未實際寫入。加 --apply 執行。")


if __name__ == "__main__":
    main()
