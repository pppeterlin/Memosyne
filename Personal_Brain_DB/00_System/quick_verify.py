#!/usr/bin/env python3
"""
Quick-Verify — The Two Mirrors

對同一 query 跑兩種設定的檢索，並用 LLM 各自生成回答，並排顯示差異。

  A. baseline-like   : 純 hybrid search（無 auto_route / 無 muse boost / 無 parent）
  B. v0.2 full       : auto_route + soft muse boost + return_parent

雖然 ChromaDB 的 HyQE 視角已經 bake 在索引裡無法關閉，
但其他 v0.2 特性（Muse Router、Parent-Child、時間擴展）都能由旗標控制。

用法：
  python3 quick_verify.py "我在 Tokyo 的生活是怎樣"
  python3 quick_verify.py                            # 互動輸入
  python3 quick_verify.py --model proxy:claude-opus-4-6 "..."
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from vectorize import search
from llm_client import chat_text


ANSWER_PROMPT = """\
你是使用者的個人記憶助理。以下是從記憶庫中檢索到的相關片段：

{context}

使用者問題：{query}

請根據這些片段回答（若資訊不足，請明確說明不足）：
"""

JUDGE_PROMPT = """\
You are the Arbiter. Two answers were produced from two retrieval configurations
for the same query. Judge which answer is more grounded, complete, and helpful.

QUERY: {query}

ANSWER A (baseline-like retrieval):
{ans_a}

ANSWER B (v0.2 full: auto_route + parent + muse boost):
{ans_b}

In 3–5 sentences:
1. 哪個較佳？(A / B / tie)
2. 主要差異在哪？
3. 是否有一方遺漏重要資訊？

回覆使用繁體中文，保持客觀精準。
"""


def format_hits(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        content = r.get("parent_section") or r.get("snippet", "")[:300]
        lines.append(
            f"[{i}] {r.get('title','')}｜{r.get('date','')}｜score={r.get('score',0):.3f}\n"
            f"    path: {r.get('path','')}\n"
            f"    {content[:400]}"
        )
    return "\n".join(lines)


def run_flavor(query: str, top_k: int, full: bool) -> list[dict]:
    if full:
        return search(
            query, top_k=top_k,
            auto_route=True, muse_mode="soft",
            return_parent=True,
        )
    else:
        return search(query, top_k=top_k)


def generate_answer(query: str, results: list[dict], model: str) -> str:
    if not results:
        return "（未命中任何記憶）"
    context = format_hits(results)
    prompt = ANSWER_PROMPT.format(context=context, query=query)
    try:
        return chat_text(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            think=False,
        ).strip()
    except Exception as e:
        return f"[LLM error] {e}"


def judge(query: str, ans_a: str, ans_b: str, model: str) -> str:
    try:
        return chat_text(
            model=model,
            messages=[{"role": "user",
                       "content": JUDGE_PROMPT.format(query=query, ans_a=ans_a, ans_b=ans_b)}],
            temperature=0,
            think=False,
        ).strip()
    except Exception as e:
        return f"[Judge error] {e}"


def print_block(title: str, body: str) -> None:
    print(f"\n{'═' * 70}\n{title}\n{'═' * 70}\n{body}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Quick A/B verify of v0.2 retrieval stack")
    ap.add_argument("query", nargs="*", help="查詢文字（省略則互動輸入）")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--model", default="proxy:claude-opus-4-6", help="LLM 模型（預設 proxy Claude Opus）")
    ap.add_argument("--no-answer", action="store_true", help="只顯示檢索結果，不生成 LLM 回答")
    ap.add_argument("--no-judge",  action="store_true", help="跳過 LLM 評審")
    args = ap.parse_args()

    if args.query:
        query = " ".join(args.query)
    else:
        try:
            query = input("你想驗收的問題：").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)
    if not query:
        print("（空查詢，結束）")
        return

    print(f"\n🔍 Query: {query}\n模型：{args.model}\n")

    # ── 兩路檢索 ──────────────────────────────────────────────
    hits_a = run_flavor(query, args.top_k, full=False)
    hits_b = run_flavor(query, args.top_k, full=True)

    print_block("A · baseline-like  (hybrid + RRF + ACT-R)", format_hits(hits_a))
    print_block("B · v0.2 full  (+ auto_route + muse boost + parent)", format_hits(hits_b))

    # ── 命中差異概覽 ──────────────────────────────────────────
    paths_a = [r.get("path") for r in hits_a]
    paths_b = [r.get("path") for r in hits_b]
    only_a = [p for p in paths_a if p not in paths_b]
    only_b = [p for p in paths_b if p not in paths_a]
    shared = [p for p in paths_a if p in paths_b]

    diff = []
    diff.append(f"共同命中：{len(shared)} 筆")
    diff.append(f"只出現在 A：{only_a}")
    diff.append(f"只出現在 B：{only_b}")
    print_block("📊 命中差異", "\n".join(diff))

    if args.no_answer:
        return

    # ── LLM 回答 ──────────────────────────────────────────────
    print("\n⏳ 生成 A 答案…")
    ans_a = generate_answer(query, hits_a, args.model)
    print_block("🅰️  Answer (baseline-like)", ans_a)

    print("\n⏳ 生成 B 答案…")
    ans_b = generate_answer(query, hits_b, args.model)
    print_block("🅱️  Answer (v0.2 full)", ans_b)

    if args.no_judge:
        return

    print("\n⏳ 評審中…")
    verdict = judge(query, ans_a, ans_b, args.model)
    print_block("⚖️  Verdict", verdict)


if __name__ == "__main__":
    main()
