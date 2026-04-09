#!/usr/bin/env python3
"""
Personal Brain DB — 互動式搜尋 REPL
用法：python3 search.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vectorize import search

HELP = """
指令：
  直接輸入文字    語義搜尋（預設 top 5）
  /top N         設定回傳筆數（如 /top 10）
  /type note     篩選類型：note / chat / bio
  /type all      取消類型篩選
  /clear         清除篩選條件
  /quit 或 q     離開
"""

def main():
    print("=== Personal Brain DB 搜尋 ===")
    print(HELP)

    top_k    = 5
    doc_type = ""

    while True:
        try:
            raw = input("搜尋 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n掰")
            break

        if not raw:
            continue
        if raw in ("/quit", "q", "quit", "exit"):
            break
        if raw.startswith("/top "):
            try:
                top_k = int(raw.split()[1])
                print(f"  → 回傳筆數設為 {top_k}")
            except ValueError:
                print("  格式：/top 5")
            continue
        if raw.startswith("/type "):
            t = raw.split()[1]
            doc_type = "" if t == "all" else t
            print(f"  → 類型篩選：{'無' if not doc_type else doc_type}")
            continue
        if raw == "/clear":
            top_k, doc_type = 5, ""
            print("  → 已重置篩選條件")
            continue
        if raw == "/help":
            print(HELP)
            continue

        results = search(raw, top_k=top_k, doc_type=doc_type)
        if not results:
            print("  無結果\n")
            continue

        print(f"\n找到 {len(results)} 筆（query: {raw!r}）\n" + "─"*55)
        for r in results:
            print(f"#{r['score']:.3f}  [{r['type']}] {r['date']}  {r['title']}")
            print(f"  {r['summary'][:90]}")
            print(f"  …{r['snippet'][:120]}…")
            print()

if __name__ == "__main__":
    main()
