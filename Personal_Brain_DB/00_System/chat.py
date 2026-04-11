#!/usr/bin/env python3
"""
Personal Brain DB — RAG Chat
支援兩種後端：
  local  — 本地 Ollama（gemma4:26b）
  cloud  — 雲端 Gemini API（GEMINI_API_KEY from .env）

Pipeline：
  向量搜尋（top N） → FlashRank 精排（留 top K） → 送進 LLM

用法：
  python3 chat.py                          # 互動式選擇後端
  python3 chat.py --backend local          # 直接使用本地
  python3 chat.py --backend cloud          # 直接使用雲端
  python3 chat.py --fetch 10 --keep 4
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

# ── 靜音雜訊 log ──────────────────────────────────────────────
warnings.filterwarnings("ignore")
os.environ["TOKENIZERS_PARALLELISM"] = "false"
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers.SentenceTransformer").setLevel(logging.ERROR)
logging.disable(logging.WARNING)

# ── VPN / Proxy 修正（Ollama localhost bypass）───────────────
os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")
for _var in ("NO_PROXY", "no_proxy"):
    _cur = os.environ.get(_var, "")
    _bypass = "localhost,127.0.0.1,::1"
    if _bypass not in _cur:
        os.environ[_var] = f"{_cur},{_bypass}".lstrip(",")

from flashrank import Ranker, RerankRequest

sys.path.insert(0, str(Path(__file__).parent))
from vectorize import search

# ─── 路徑 ───────────────────────────────────────────────────

ROOT            = Path(__file__).parent.parent.parent   # personal-memory/
ENV_FILE        = ROOT / ".env"
PROFILE_DIR     = Path(__file__).parent.parent / "10_Profile"
FLASHRANK_CACHE = Path(__file__).parent / "flashrank_cache"

# ─── 全域設定 ────────────────────────────────────────────────

LOCAL_MODEL      = "gemma4:26b"
CLOUD_MODEL      = "gemini-2.0-flash-lite"   # 免費額度最高的 Gemini 模型

FETCH_K          = 10
KEEP_K           = 4
RERANK_THRESHOLD = 0.05
HISTORY_WINDOW   = 8

PROFILE_BODY_LIM = 250
SNIPPET_LIM      = 300

SYSTEM_PROMPT = """你是林君的個人 AI 助理，擁有存取他個人記憶庫的能力。
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


# ─── FlashRank Reranker ──────────────────────────────────────

@lru_cache(maxsize=1)
def get_ranker() -> Ranker:
    return Ranker(
        model_name="ms-marco-MiniLM-L-12-v2",
        cache_dir=str(FLASHRANK_CACHE),
    )


def rerank(query: str, results: list[dict], keep: int, threshold: float) -> list[dict]:
    if not results:
        return []
    passages = [{"id": i, "text": r["snippet"], "meta": r} for i, r in enumerate(results)]
    req      = RerankRequest(query=query, passages=passages)
    reranked = get_ranker().rerank(req)
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
        content    = p.read_text(encoding="utf-8")
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
    raw_results = search(query, top_k=fetch_k)
    ranked      = rerank(query, raw_results, keep=keep_k, threshold=RERANK_THRESHOLD)
    parts       = ["=== 使用者基本資料 ===\n" + get_profile_context()]
    if ranked:
        parts.append("=== 相關記憶片段（精排後）===")
        for r in ranked:
            src     = f"[{r['type']}][{r['date']}] {r['title']}"
            snippet = r["snippet"][:SNIPPET_LIM]
            parts.append(f"來源：{src}  (rerank={r['rerank_score']:.3f})\n{snippet}")
    else:
        parts.append("（未找到相關記憶片段）")
    return "\n\n".join(parts), ranked, raw_results


# ─── 對話歷史滑動窗口 ────────────────────────────────────────

def trim_history(messages: list, window: int) -> list:
    system  = [m for m in messages if m["role"] == "system"]
    turns   = [m for m in messages if m["role"] != "system"]
    trimmed = turns[-(window * 2):]
    return system + trimmed


# ─── 後端：本地 Ollama ───────────────────────────────────────

def chat_once_local(
    messages: list,
    query: str,
    fetch_k: int,
    keep_k: int,
    stream: bool,
    model: str,
) -> tuple[str, list[dict], list[dict]]:
    import ollama

    context, ranked, raw = build_context(query, fetch_k, keep_k)
    user_content = f"【記憶片段】\n{context}\n\n【問題】\n{query}"
    messages.append({"role": "user", "content": user_content})

    full_reply = ""
    MAX_RETRY  = 3

    for attempt in range(1, MAX_RETRY + 1):
        try:
            if stream:
                with CatSpinner():
                    gen         = ollama.chat(model=model, messages=messages, stream=True, think=False)
                    first_chunk = next(gen, None)
                print(f"\n{model} > ", end="", flush=True)
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
                print(f"\n{model} > {full_reply}\n")
            break

        except Exception as e:
            if "502" in str(e) and attempt < MAX_RETRY:
                print(f"\r⚠️  連線抖動（{attempt}/{MAX_RETRY}），重試中...", end="", flush=True)
                time.sleep(1.5)
                full_reply = ""
                continue
            raise

    messages[-1] = {"role": "user", "content": query}
    messages.append({"role": "assistant", "content": full_reply})
    messages[:] = trim_history(messages, HISTORY_WINDOW)
    return full_reply, ranked, raw


# ─── 後端：雲端 Gemini API ───────────────────────────────────

def _to_gemini_history(messages: list) -> list[dict]:
    """
    將 Ollama 格式 messages 轉為 Gemini contents 格式。
    system message 不進 contents（另外透過 config.system_instruction 傳入）。
    assistant → role="model"
    """
    history = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "model" if m["role"] == "assistant" else "user"
        history.append({"role": role, "parts": [{"text": m["content"]}]})
    return history


def chat_once_cloud(
    messages: list,
    query: str,
    fetch_k: int,
    keep_k: int,
    stream: bool,
    client,           # google.genai.Client
    model: str,
) -> tuple[str, list[dict], list[dict]]:
    from google.genai import types

    context, ranked, raw = build_context(query, fetch_k, keep_k)
    user_content = f"【記憶片段】\n{context}\n\n【問題】\n{query}"

    system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
    history    = _to_gemini_history(messages)
    history.append({"role": "user", "parts": [{"text": user_content}]})

    config = types.GenerateContentConfig(system_instruction=system_msg)

    full_reply = ""

    try:
        if stream:
            with CatSpinner():
                gen   = client.models.generate_content_stream(
                    model=model, contents=history, config=config
                )
                first = next(gen, None)
            print(f"\n{model} > ", end="", flush=True)
            if first and first.text:
                print(first.text, end="", flush=True)
                full_reply += first.text
            for chunk in gen:
                if chunk.text:
                    print(chunk.text, end="", flush=True)
                    full_reply += chunk.text
            print("\n")
        else:
            with CatSpinner():
                resp = client.models.generate_content(
                    model=model, contents=history, config=config
                )
            full_reply = resp.text or ""
            print(f"\n{model} > {full_reply}\n")

    except Exception as e:
        raise RuntimeError(f"Gemini API 錯誤：{e}") from e

    messages.append({"role": "user",      "content": query})
    messages.append({"role": "assistant", "content": full_reply})
    messages[:] = trim_history(messages, HISTORY_WINDOW)
    return full_reply, ranked, raw


# ─── 後端選擇 ────────────────────────────────────────────────

def load_gemini_client():
    """從 .env 讀取 GEMINI_API_KEY，初始化 google.genai.Client。"""
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print(f"[ERROR] 找不到 GEMINI_API_KEY（讀取自 {ENV_FILE}）")
        print("  請確認 .env 裡有：GEMINI_API_KEY=your_key_here")
        sys.exit(1)

    from google import genai
    return genai.Client(api_key=api_key)


def pick_backend(forced: str) -> tuple[str, str, object | None]:
    """
    回傳 (backend, model, gemini_client_or_None)。
    forced: "local" | "cloud" | "" (互動式選擇)
    """
    if forced == "local":
        choice = "1"
    elif forced == "cloud":
        choice = "2"
    else:
        print("┌─────────────────────────────────────────┐")
        print("│  選擇 LLM 後端                           │")
        print("│  [1] 本地 Ollama（gemma4:26b）           │")
        print("│  [2] 雲端 Gemini（gemini-2.0-flash-lite）│")
        print("└─────────────────────────────────────────┘")
        try:
            choice = input("選擇 [1/2]（預設 1）: ").strip() or "1"
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)

    if choice == "2":
        client = load_gemini_client()
        return "cloud", CLOUD_MODEL, client
    else:
        return "local", LOCAL_MODEL, None


# ─── 主程式 ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend",    type=str, default="",      help="local / cloud（省略則互動選擇）")
    parser.add_argument("--model",      type=str, default="",      help="覆寫模型名稱")
    parser.add_argument("--fetch",      type=int, default=FETCH_K, help=f"向量搜尋初撈筆數（預設 {FETCH_K}）")
    parser.add_argument("--keep",       type=int, default=KEEP_K,  help=f"rerank 後保留筆數（預設 {KEEP_K}）")
    parser.add_argument("--no-stream",  action="store_true",       help="關閉 streaming")
    args = parser.parse_args()

    stream  = not args.no_stream
    fetch_k = args.fetch
    keep_k  = args.keep

    # ── 後端選擇 ────────────────────────────────────────────
    backend, model, gemini_client = pick_backend(args.backend)
    if args.model:
        model = args.model

    # ── 預載 reranker ────────────────────────────────────────
    print("\n載入 FlashRank reranker...", end="", flush=True)
    get_ranker()
    print(" OK\n")

    messages: list    = [{"role": "system", "content": SYSTEM_PROMPT}]
    last_ranked: list = []
    last_raw: list    = []

    backend_label = f"Ollama / {model}" if backend == "local" else f"Gemini / {model}"
    print(f"=== Personal Brain RAG Chat ===")
    print(f"後端：{backend_label}  向量搜 {fetch_k} → rerank 留 {keep_k}  "
          f"歷史窗口：{HISTORY_WINDOW} 輪  Streaming：{stream}")
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
                    rs = r.get("rerank_score", "-")
                    vs = r.get("score", "-")
                    print(f"  rerank={rs}  vec={vs}  [{r['type']}] {r['date']}  {r['title']}")
                print()
            continue
        if query == "/hist":
            turns = [m for m in messages if m["role"] != "system"]
            print(f"  目前歷史：{len(turns)//2} 輪（上限 {HISTORY_WINDOW} 輪）\n")
            continue

        try:
            if backend == "cloud":
                _, last_ranked, last_raw = chat_once_cloud(
                    messages, query, fetch_k, keep_k, stream, gemini_client, model
                )
            else:
                _, last_ranked, last_raw = chat_once_local(
                    messages, query, fetch_k, keep_k, stream, model
                )
        except Exception as e:
            err = str(e)
            if "502" in err:
                print(f"\n[ERROR] Ollama 連線失敗（502）。"
                      f"可能是 VPN TUN 模式攔截了 localhost 連線。\n")
            else:
                print(f"\n[ERROR] {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    main()
