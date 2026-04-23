#!/usr/bin/env python3
"""
Query Decomposition — Phase 4.7

將複雜 query（含多實體、多意圖）拆成若干子 query，各自走 RRF 檢索後再合併。
解決「單一向量表徵難以同時涵蓋多面向」的問題。

範例：
  Q: "2025 年我在 Tokyo 和 friend-A 討論過的專案"
  →  ["2025", "Tokyo friend-A", "專案討論"]

觸發條件：查詢長度 >= DECOMPOSE_MIN_LEN 或語句含多個 entity / 連接詞。
"""
from __future__ import annotations
import json
import re
from typing import List

DECOMPOSE_MIN_LEN = 15
DECOMPOSE_MAX_SUBQUERIES = 4

_CONNECTIVE_RE = re.compile(r"[，、和與跟還有,&]| and | or | with | about ")


def is_complex(query: str) -> bool:
    """啟發式判斷是否值得拆解（避免無謂的 LLM 成本）。"""
    q = query.strip()
    if len(q) >= DECOMPOSE_MIN_LEN:
        return True
    # 連接詞或多個中英文實體片段
    if len(_CONNECTIVE_RE.findall(q)) >= 1:
        return True
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][\w-]+", q)
    return len(tokens) >= 3


DECOMPOSE_PROMPT = """\
You are a query-decomposition assistant for a personal memory retrieval system.
Given a user query, split it into 2-4 concise sub-queries, each focused on
ONE dimension (time / person / place / topic / action). Sub-queries must
together cover the original intent, with minimal overlap.

Output ONLY a JSON array of strings. No explanation.

Examples:
Q: "2025 年我在 Tokyo 和 friend-A 討論過的專案"
A: ["2025", "Tokyo friend-A", "專案討論"]

Q: "上週末跟家人吃飯的餐廳"
A: ["上週末", "家人 吃飯", "餐廳"]

Q: "我的萬年龜眼鏡型號"
A: ["萬年龜 眼鏡", "眼鏡型號"]

Now decompose this query:
Q: {query}
A: """


def decompose(query: str, model: str = "proxy:claude-opus-4-6") -> List[str]:
    """呼叫 LLM 拆解；失敗則回傳原 query 單項。"""
    from llm_client import chat_text
    prompt = DECOMPOSE_PROMPT.format(query=query)
    try:
        raw = chat_text(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            think=False,
        ).strip()
    except Exception:
        return [query]

    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < 0:
        return [query]
    try:
        subs = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return [query]

    subs = [str(s).strip() for s in subs if isinstance(s, (str,)) and str(s).strip()]
    subs = subs[:DECOMPOSE_MAX_SUBQUERIES]
    if not subs:
        return [query]
    return subs


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "2025 年我在 Tokyo 和 friend-A 討論過的專案"
    print(f"Q: {q}")
    print(f"Complex? {is_complex(q)}")
    print(f"Decomposed: {decompose(q)}")
