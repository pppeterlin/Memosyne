#!/usr/bin/env python3
"""
Personal Brain DB — RAG Chat
支援兩種後端：
  local  — 本地 Ollama（gemma4:26b）
  cloud  — 雲端 Gemini API（GEMINI_API_KEY from .env）

Pipeline：
  [Query Planner] → 多路向量搜尋（top N） → FlashRank 精排（留 top K） → 送進 LLM

  Query Planner（可用 --no-plan 關閉）：
    分析問題意圖，將「列舉/探索型」問題展開成 2~4 個子查詢，
    再合併搜尋結果，解決廣泛問題找不到記憶的問題。

用法：
  python3 chat.py                          # 互動式選擇後端
  python3 chat.py --backend local          # 直接使用本地
  python3 chat.py --backend cloud          # 直接使用雲端
  python3 chat.py --fetch 10 --keep 4
  python3 chat.py --no-stream
  python3 chat.py --no-plan               # 關閉 Query Planner（直接用原始問題搜尋）
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

# ─── Query Planner 設定 ──────────────────────────────────────
PLAN_ENABLED     = True      # 預設開啟，可用 --no-plan 關閉
PLAN_MAX_QUERIES = 4         # Planner 最多展開幾個子查詢

SYSTEM_PROMPT = """你是使用者的個人 AI 助理，擁有存取他個人記憶庫的能力。
回答規則：
- 繁體中文回答
- 有相關記憶片段時，引用並說明來源（如「根據你 2025 年 3 月的手札...」）
- 沒有直接資訊時誠實說明，再用一般知識補充
- 簡潔直接，善用對話歷史上下文
"""

# ─── Loading 動畫 ───────────────────────────────────────────

# Phase 1：Oracle 分析問題意圖
ORACLE_FRAMES = [
    "  🔮 ◐  Oracle is divining the search path...",
    "  🔮 ◓  Oracle is divining the search path...",
    "  🔮 ◑  Oracle is divining the search path...",
    "  🔮 ◒  Oracle is divining the search path...",
]

# Phase 2：The Spring — 搜尋記憶庫
SPRING_FRAMES = [
    "  🌊 ≋  The Spring stirs, seeking memories...",
    "  🌊 ≈  The Spring stirs, seeking memories...",
    "  🌊 ～  The Spring stirs, seeking memories...",
    "  🌊 ≈  The Spring stirs, seeking memories...",
]

# Phase 3：貓咪 — LLM 生成回答
CAT_FRAMES = [
    "  ฅ(=^･ω･^=)ฅ  正在翻記憶...",
    "  ฅ(=^･ω･^=)ノ  正在翻記憶...",
    "  ฅ(=^･-･^=)ฅ  正在翻記憶...",
    "  ฅ(=^･ω･^=)ฅ  正在翻記憶...",
    " ／ฅ(=^･ω･^=)  正在翻記憶...",
    "  ฅ(=^･ω･^=)ฅ  正在翻記憶...",
]


class _Spinner:
    """通用 context-manager 動畫，接受自訂 frames 列表。"""
    def __init__(self, frames: list[str], interval: float = 0.2):
        self._frames   = frames
        self._interval = interval
        self._stop     = threading.Event()
        self._thread   = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        pad = max(len(f) for f in self._frames) + 2
        for frame in itertools.cycle(self._frames):
            if self._stop.is_set():
                break
            print(f"\r{frame}", end="", flush=True)
            time.sleep(self._interval)
        print("\r" + " " * pad + "\r", end="", flush=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()


def OracleSpinner(): return _Spinner(ORACLE_FRAMES, interval=0.25)
def SpringSpinner(): return _Spinner(SPRING_FRAMES, interval=0.22)


class CatSpinner:
    def __init__(self):
        self._inner = _Spinner(CAT_FRAMES, interval=0.18)

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *_):
        self._inner.__exit__()


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


# ─── Query Planner ──────────────────────────────────────────
#
# 輕量的前置步驟：在正式搜尋記憶之前，讓 LLM 把問題轉成
# 更適合向量/BM25 搜尋的關鍵詞組。
#
# 適用情境（enumerate 型問題）：
#   「我去過哪些國家」「這幾年有哪些工作」「我有沒有提到過...」
# 不適用（direct 型問題）：
#   「我 2025 年幾月到Osaka」「上次見到誰」→ 直接用原始 query

PLANNER_PROMPT = """\
你是一個記憶搜尋助理。請分析使用者的問題，決定要如何搜尋個人記憶庫。

規則：
1. 判斷問題類型：
   - "enumerate"：需要廣撒網（如「去過哪些」「有哪些」「這幾年」「有沒有」「什麼時候」）
   - "direct"：直接問某件具體事情，用原始問題搜尋即可

2. 若為 enumerate，拆出 2~4 個搜尋關鍵詞組（繁體中文，短詞），每個關鍵詞組 2~6 字。
   關鍵詞組要覆蓋不同角度，例如地名、動詞、情境詞。

3. 只輸出 JSON，不要多餘說明：
   {{"type": "direct", "queries": ["原始問題"]}}
   或
   {{"type": "enumerate", "queries": ["關鍵詞1", "關鍵詞2", "關鍵詞3"]}}

使用者問題：{question}
"""


def plan_query_local(question: str, model: str) -> list[str]:
    """用本地 Ollama 做 query planning，回傳搜尋關鍵詞列表。"""
    import json
    import ollama

    prompt = PLANNER_PROMPT.format(question=question)
    try:
        resp = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            think=False,
        )
        raw = resp["message"]["content"].strip()
        # 擷取 JSON（有時 LLM 會在前後加說明文字）
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            queries = parsed.get("queries", [])
            if queries and isinstance(queries, list):
                return [str(q).strip() for q in queries[:PLAN_MAX_QUERIES] if q]
    except Exception:
        pass
    return [question]


def plan_query_cloud(question: str, client) -> list[str]:
    """用 Gemini 做 query planning，回傳搜尋關鍵詞列表。"""
    import json
    from google.genai import types

    prompt = PLANNER_PROMPT.format(question=question)
    try:
        resp = client.models.generate_content(
            model=CLOUD_MODEL,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        raw = (resp.text or "").strip()
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            queries = parsed.get("queries", [])
            if queries and isinstance(queries, list):
                return [str(q).strip() for q in queries[:PLAN_MAX_QUERIES] if q]
    except Exception:
        pass
    return [question]


def plan_query(question: str, backend: str, model: str, gemini_client) -> list[str]:
    """
    根據後端呼叫對應的 planner。
    若 planning 失敗（任何原因），fallback 回 [question]。
    """
    if backend == "cloud" and gemini_client is not None:
        return plan_query_cloud(question, gemini_client)
    return plan_query_local(question, model)


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


def build_context(
    query: str,
    fetch_k: int,
    keep_k: int,
    extra_queries: list[str] | None = None,
) -> tuple[str, list[dict], list[dict]]:
    """
    搜尋記憶並建立 LLM context。

    extra_queries：Query Planner 展開的子查詢（不含原始 query 自身）。
    若有 extra_queries，各路結果會在 RRF 合併後再做 rerank，
    讓廣泛型問題（如「去過哪些國家」）也能找到分散的記憶片段。
    """
    from vectorize import _rrf_merge_multi

    # ── 各路搜尋 ─────────────────────────────────────────────
    all_queries = [query] + (extra_queries or [])
    all_result_lists: list[list[dict]] = []
    for q in all_queries:
        results = search(q, top_k=fetch_k)
        if results:
            all_result_lists.append(results)

    if not all_result_lists:
        raw_results = []
    elif len(all_result_lists) == 1:
        raw_results = all_result_lists[0]
    else:
        raw_results = _rrf_merge_multi(all_result_lists)

    # ── Rerank（以原始問題作為 rerank 的 query）─────────────
    ranked = rerank(query, raw_results, keep=keep_k, threshold=RERANK_THRESHOLD)

    # ── 組合 context ─────────────────────────────────────────
    parts = ["=== 使用者基本資料 ===\n" + get_profile_context()]
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


# ─── 顯示名稱 ───────────────────────────────────────────────

def make_reply_label(backend: str, model: str) -> str:
    """
    從 backend + model 生成人性化的回覆前綴。
      local  + gemma4:26b           → "Gemma4"
      local  + llama3.2:3b          → "Llama3.2"
      cloud  + gemini-2.0-flash-lite → "Gemini"
      cloud  + gemini-1.5-pro        → "Gemini"
    若使用者透過 --model 傳入自訂名稱，直接使用（去掉 tag）。
    """
    if backend == "local":
        base = model.split(":")[0]          # "gemma4:26b" → "gemma4"
        return base[0].upper() + base[1:]   # → "Gemma4"
    else:
        # cloud：取第一段（"gemini-2.0-flash-lite" → "Gemini"）
        return model.split("-")[0].capitalize()


# ─── 後端：本地 Ollama ───────────────────────────────────────

def chat_once_local(
    messages: list,
    query: str,
    fetch_k: int,
    keep_k: int,
    stream: bool,
    model: str,
    reply_label: str = "",
    use_plan: bool = True,
    gemini_client=None,
) -> tuple[str, list[dict], list[dict], list[str]]:
    import ollama

    # ── Query Planning ────────────────────────────────────────
    planned_queries: list[str] = []
    if use_plan:
        with OracleSpinner():
            all_queries = plan_query(query, "local", model, gemini_client)
        # 若 planner 只回傳原始問題（direct 型），extra_queries 為空
        planned_queries = [q for q in all_queries if q != query]

    with SpringSpinner():
        context, ranked, raw = build_context(query, fetch_k, keep_k, extra_queries=planned_queries)
    user_content = f"【記憶片段】\n{context}\n\n【問題】\n{query}"
    messages.append({"role": "user", "content": user_content})

    label      = reply_label or model
    full_reply = ""
    MAX_RETRY  = 3

    for attempt in range(1, MAX_RETRY + 1):
        try:
            if stream:
                with CatSpinner():
                    gen         = ollama.chat(model=model, messages=messages, stream=True, think=False)
                    first_chunk = next(gen, None)
                print(f"\n{label} > ", end="", flush=True)
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
                print(f"\n{label} > {full_reply}\n")
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
    return full_reply, ranked, raw, planned_queries


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
    reply_label: str = "",
    use_plan: bool = True,
) -> tuple[str, list[dict], list[dict], list[str]]:
    from google.genai import types

    # ── Query Planning ────────────────────────────────────────
    planned_queries: list[str] = []
    if use_plan:
        with OracleSpinner():
            all_queries = plan_query(query, "cloud", model, client)
        planned_queries = [q for q in all_queries if q != query]

    label   = reply_label or model
    with SpringSpinner():
        context, ranked, raw = build_context(query, fetch_k, keep_k, extra_queries=planned_queries)
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
            print(f"\n{label} > ", end="", flush=True)
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
            print(f"\n{label} > {full_reply}\n")

    except Exception as e:
        raise RuntimeError(f"Gemini API 錯誤：{e}") from e

    messages.append({"role": "user",      "content": query})
    messages.append({"role": "assistant", "content": full_reply})
    messages[:] = trim_history(messages, HISTORY_WINDOW)
    return full_reply, ranked, raw, planned_queries


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
    parser.add_argument("--no-plan",    action="store_true",       help="關閉 Query Planner（直接用原始問題搜尋）")
    args = parser.parse_args()

    stream   = not args.no_stream
    fetch_k  = args.fetch
    keep_k   = args.keep
    use_plan = not args.no_plan

    # ── 後端選擇 ────────────────────────────────────────────
    backend, model, gemini_client = pick_backend(args.backend)
    if args.model:
        model = args.model

    reply_label = make_reply_label(backend, model)

    # ── 預載 reranker ────────────────────────────────────────
    print("\n載入 FlashRank reranker...", end="", flush=True)
    get_ranker()
    print(" OK\n")

    messages: list      = [{"role": "system", "content": SYSTEM_PROMPT}]
    last_ranked: list   = []
    last_raw: list      = []
    last_planned: list  = []   # 上一輪 planner 展開的子查詢（for /plan 指令）

    plan_label    = "開啟" if use_plan else "關閉"
    backend_label = f"Ollama / {model}" if backend == "local" else f"Gemini / {model}"
    print(f"=== Personal Brain RAG Chat ===")
    print(f"後端：{backend_label}  向量搜 {fetch_k} → rerank 留 {keep_k}  "
          f"歷史窗口：{HISTORY_WINDOW} 輪  Streaming：{stream}  Query Planner：{plan_label}")
    print("指令：/ctx 看記憶來源  /plan 看上輪搜尋計畫  /hist 看歷史輪數  /clear 清歷史  q 離開\n")

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
        if query == "/plan":
            if not use_plan:
                print("  （Query Planner 已關閉，使用 --no-plan 啟動時不會展開子查詢）\n")
            elif not last_planned:
                print("  （上一輪為 direct 型問題，Planner 未展開子查詢）\n")
            else:
                print(f"\n上一輪 Planner 展開了 {len(last_planned)} 個子查詢：")
                for i, q in enumerate(last_planned, 1):
                    print(f"  [{i}] {q}")
                print()
            continue
        if query == "/hist":
            turns = [m for m in messages if m["role"] != "system"]
            print(f"  目前歷史：{len(turns)//2} 輪（上限 {HISTORY_WINDOW} 輪）\n")
            continue

        try:
            if backend == "cloud":
                _, last_ranked, last_raw, last_planned = chat_once_cloud(
                    messages, query, fetch_k, keep_k, stream, gemini_client, model,
                    reply_label=reply_label,
                    use_plan=use_plan,
                )
            else:
                _, last_ranked, last_raw, last_planned = chat_once_local(
                    messages, query, fetch_k, keep_k, stream, model,
                    reply_label=reply_label,
                    use_plan=use_plan,
                    gemini_client=gemini_client,
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
