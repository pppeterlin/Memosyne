#!/usr/bin/env python3
"""
Personal Brain DB — RAG Chat
Ollama (gemma4:26b) + 個人記憶庫 + FlashRank reranker + 對話歷史滑動窗口

Pipeline：
  向量搜尋（top N） → FlashRank 精排（留 top K，低於 threshold 剔掉） → 塞進 Gemma4

用法：
  python3 chat.py
  python3 chat.py --fetch 10 --keep 4   # 向量搜 10 筆，精排後保留最多 4 筆
  python3 chat.py --no-stream
"""

import argparse
import os
import sys
import logging
import threading
import time
import itertools
import warnings
from pathlib import Path
from functools import lru_cache

# ── 靜音所有雜訊 log（sentence_transformers / huggingface / httpx）──
warnings.filterwarnings("ignore")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# sentence-transformers 用 logger level 決定要不要顯示 "Batches:" 進度條
# （see SentenceTransformer.py line 309: show_progress_bar 預設取決於
#  logger.getEffectiveLevel() == INFO）→ 提高到 ERROR 才能關掉
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers.SentenceTransformer").setLevel(logging.ERROR)
logging.disable(logging.WARNING)

import ollama
from flashrank import Ranker, RerankRequest

sys.path.insert(0, str(Path(__file__).parent))
from vectorize import search

# ─── 全域設定 ────────────────────────────────────────────────

MODEL       = "gemma4:26b"
FETCH_K     = 10      # 向量搜尋初撈筆數（大一點給 reranker 更多候選）
KEEP_K      = 4       # rerank 後最多保留筆數
RERANK_THRESHOLD = 0.05   # 低於此分數的 chunk 直接丟棄
HISTORY_WINDOW   = 8      # 保留最近幾輪對話（不含 system message）

PROFILE_DIR      = Path(__file__).parent.parent / "10_Profile"
PROFILE_BODY_LIM = 250    # 每個 Profile 檔案最多帶入字數
SNIPPET_LIM      = 300    # 每筆 chunk 最多字數

FLASHRANK_CACHE  = Path(__file__).parent / "flashrank_cache"

SYSTEM_PROMPT = """你是使用者的個人 AI 助理，擁有存取他個人記憶庫的能力。
回答規則：
- 繁體中文回答
- 有相關記憶片段時，引用並說明來源（如「根據你 2025 年 3 月的手札...」）
- 沒有直接資訊時誠實說明，再用一般知識補充
- 簡潔直接，善用對話歷史上下文
"""

# ─── 貓咪 Loading 動畫 ──────────────────────────────────────

CAT_FRAMES = [
    "  ฅ(=^･ω･^=)ฅ  正在翻記憶...",
    "  ฅ(=^･ω･^=)ノ  正在翻記憶...",
    "  ฅ(=^･-･^=)ฅ  正在翻記憶...",
    "  ฅ(=^･ω･^=)ฅ  正在翻記憶...",
    " ／ฅ(=^･ω･^=)  正在翻記憶...",
    "  ฅ(=^･ω･^=)ฅ  正在翻記憶...",
]

class CatSpinner:
    def __init__(self):
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        for frame in itertools.cycle(CAT_FRAMES):
            if self._stop.is_set():
                break
            print(f"\r{frame}", end="", flush=True)
            time.sleep(0.18)
        print("\r" + " " * 45 + "\r", end="", flush=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()


# ─── FlashRank Reranker（singleton，避免重複載入模型）────────

@lru_cache(maxsize=1)
def get_ranker() -> Ranker:
    return Ranker(
        model_name="ms-marco-MiniLM-L-12-v2",
        cache_dir=str(FLASHRANK_CACHE),
    )


def rerank(query: str, results: list[dict], keep: int, threshold: float) -> list[dict]:
    """
    用 FlashRank 對向量搜尋結果精排。
    - 剔除 score < threshold 的 chunk
    - 最多保留 keep 筆
    回傳：精排後的 results（加上 rerank_score 欄位）
    """
    if not results:
        return []

    passages = [
        {"id": i, "text": r["snippet"], "meta": r}
        for i, r in enumerate(results)
    ]
    req      = RerankRequest(query=query, passages=passages)
    reranked = get_ranker().rerank(req)

    # 過濾 + 截斷
    kept = []
    for item in reranked:
        if item["score"] < threshold:
            continue
        r = item["meta"].copy()
        r["rerank_score"] = round(float(item["score"]), 4)
        kept.append(r)
        if len(kept) >= keep:
            break

    return kept


# ─── Profile Context ─────────────────────────────────────────

def get_profile_context() -> str:
    parts = []
    for fname in ("bio.md", "career.md", "family_pets.md"):
        p = PROFILE_DIR / fname
        if not p.exists():
            continue
        content  = p.read_text(encoding="utf-8")
        summary, body_start = "", 0
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                for line in content[3:end].split("\n"):
                    if line.strip().startswith("summary:"):
                        summary = line.split(":", 1)[1].strip().strip('"')
                body_start = end + 3
        body = content[body_start:].strip()[:PROFILE_BODY_LIM]
        if summary or body:
            parts.append(f"[{fname}]\n{summary}\n{body}")
    return "\n\n".join(parts)


def build_context(query: str, fetch_k: int, keep_k: int) -> tuple[str, list[dict], list[dict]]:
    """
    回傳 (context 字串, 精排後結果, 原始搜尋結果)
    """
    # 1. 向量搜尋（多撈一些給 reranker 選）
    raw_results = search(query, top_k=fetch_k)

    # 2. FlashRank 精排
    ranked = rerank(query, raw_results, keep=keep_k, threshold=RERANK_THRESHOLD)

    # 3. 組合 context
    parts = ["=== 使用者基本資料 ===\n" + get_profile_context()]

    if ranked:
        parts.append("=== 相關記憶片段（精排後）===")
        for r in ranked:
            src     = f"[{r['type']}][{r['date']}] {r['title']}"
            snippet = r["snippet"][:SNIPPET_LIM]
            score_info = f"rerank={r['rerank_score']:.3f}"
            parts.append(f"來源：{src}  ({score_info})\n{snippet}")
    else:
        parts.append("（未找到相關記憶片段）")

    return "\n\n".join(parts), ranked, raw_results


# ─── 對話歷史滑動窗口 ────────────────────────────────────────

def trim_history(messages: list, window: int) -> list:
    """
    保留 system message + 最近 window 輪（每輪 = user + assistant 共 2 條）。
    超出的舊對話靜默丟棄。
    """
    system = [m for m in messages if m["role"] == "system"]
    turns  = [m for m in messages if m["role"] != "system"]
    # 每輪兩條，最多保留 window * 2 條
    trimmed = turns[-(window * 2):]
    if len(turns) > len(trimmed):
        trimmed_count = (len(turns) - len(trimmed)) // 2
        # 不印提示，靜默丟棄
    return system + trimmed


# ─── 對話核心 ────────────────────────────────────────────────

def chat_once(
    messages: list,
    query: str,
    fetch_k: int,
    keep_k: int,
    stream: bool,
    model: str,
) -> tuple[str, list[dict], list[dict]]:
    """回傳 (reply, ranked_results, raw_results)"""
    context, ranked, raw = build_context(query, fetch_k, keep_k)

    user_content = f"【記憶片段】\n{context}\n\n【問題】\n{query}"
    messages.append({"role": "user", "content": user_content})

    full_reply = ""

    # think=False 是 ollama 頂層參數（不是 options 裡面）
    # 關閉 gemma4 的 thinking mode，避免 streaming 時 502
    if stream:
        with CatSpinner():
            gen         = ollama.chat(model=model, messages=messages, stream=True, think=False)
            first_chunk = next(gen, None)
        print("\nGemma4 > ", end="", flush=True)
        if first_chunk:
            t = first_chunk["message"]["content"]
            print(t, end="", flush=True)
            full_reply += t
        for chunk in gen:
            t = chunk["message"]["content"]
            print(t, end="", flush=True)
            full_reply += t
        print("\n")
    else:
        with CatSpinner():
            resp = ollama.chat(model=model, messages=messages, stream=False, think=False)
        full_reply = resp["message"]["content"]
        print(f"\nGemma4 > {full_reply}\n")

    # 歷史存乾淨問題（不含 context），再套滑動窗口
    messages[-1] = {"role": "user", "content": query}
    messages.append({"role": "assistant", "content": full_reply})
    messages[:] = trim_history(messages, HISTORY_WINDOW)

    return full_reply, ranked, raw


# ─── 主程式 ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch",     type=int,  default=FETCH_K,  help=f"向量搜尋初撈筆數（預設 {FETCH_K}）")
    parser.add_argument("--keep",      type=int,  default=KEEP_K,   help=f"rerank 後保留筆數（預設 {KEEP_K}）")
    parser.add_argument("--no-stream", action="store_true",         help="關閉 streaming")
    parser.add_argument("--model",     type=str,  default=MODEL,    help="Ollama 模型名稱")
    args = parser.parse_args()

    stream  = not args.no_stream
    model   = args.model
    fetch_k = args.fetch
    keep_k  = args.keep

    # 預載 reranker（避免第一次問題有延遲）
    print("載入 FlashRank reranker...", end="", flush=True)
    get_ranker()
    print(" OK\n")

    messages: list     = [{"role": "system", "content": SYSTEM_PROMPT}]
    last_ranked: list  = []
    last_raw: list     = []

    print(f"=== Personal Brain RAG Chat ===")
    print(f"模型：{model}  向量搜 {fetch_k} → rerank 留 {keep_k}  歷史窗口：{HISTORY_WINDOW} 輪  Streaming：{stream}")
    print("指令：/ctx 看記憶來源  /hist 看歷史輪數  /clear 清歷史  q 離開\n")

    while True:
        try:
            query = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n掰～ ฅ(=^･ω･^=)ฅ")
            break

        if not query:
            continue
        if query in ("q", "quit", "exit"):
            print("掰～ ฅ(=^･ω･^=)ฅ")
            break
        if query == "/clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("  → 對話歷史已清空\n")
            continue
        if query == "/ctx":
            if not last_ranked:
                print("  （尚無結果）\n")
            else:
                print(f"\n精排後保留 {len(last_ranked)} 筆（原始 {len(last_raw)} 筆）：")
                for r in last_ranked:
                    rs = r.get('rerank_score', '-')
                    vs = r.get('score', '-')
                    print(f"  rerank={rs}  vec={vs}  [{r['type']}] {r['date']}  {r['title']}")
                print()
            continue
        if query == "/hist":
            turns = [m for m in messages if m["role"] != "system"]
            print(f"  目前歷史：{len(turns)//2} 輪（上限 {HISTORY_WINDOW} 輪）\n")
            continue

        try:
            _, last_ranked, last_raw = chat_once(
                messages, query, fetch_k, keep_k, stream, model
            )
        except Exception as e:
            import traceback
            print(f"\n[ERROR] {type(e).__name__}: {e}\n")
            traceback.print_exc()
            print()


if __name__ == "__main__":
    main()
