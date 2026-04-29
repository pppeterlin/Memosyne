#!/usr/bin/env python3
"""
LLM Client — 統一的 LLM 呼叫介面（Ollama / OpenRouter 雙後端）
===============================================================

目的：讓 enrich / vectorize / slumber / augury / chat 等模組
透過同一個介面呼叫 LLM，本地或雲端自由切換。

Provider 判定優先序：
    1. 明確 prefix：model="openrouter:google/gemma-3-27b-it"
    2. 環境變數：LLM_PROVIDER=openrouter
    3. 模型名有 "/"（OpenRouter 格式 "org/model"）→ openrouter
    4. 預設 → ollama

API Key 載入：
    OpenRouter key 依序從：
      1. 環境變數 OPENROUTER_API_KEY
      2. repo 根目錄 `openrouter-key` 檔案
      3. 環境變數 OPENROUTER_KEY（備用名）

環境變數（建議）：
    LLM_PROVIDER=ollama|openrouter       # 強制指定後端
    OPENROUTER_API_KEY=sk-or-v1-xxxx     # 金鑰
    OPENROUTER_BASE_URL=...              # 自訂端點（少用）

用法：
    from llm_client import chat_text

    reply = chat_text(
        model="gemma4:26b",  # 或 "openrouter:google/gemma-3-27b-it"
        messages=[{"role":"user","content":"..."}],
        temperature=0,
    )
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterator

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

OPENROUTER_PREFIX = "openrouter:"
OPENROUTER_BASE_URL_DEFAULT = "https://openrouter.ai/api/v1"

# Proxy（OpenAI 相容的本地/自架反代，如 aiclient-2-api）
PROXY_PREFIX = "proxy:"
PROXY_BASE_URL_DEFAULT = "http://localhost:3000/claude-kiro-oauth/v1"
PROXY_KEY_FILE_DEFAULT = "ANTHROPIC_API_KEY"

# OpenRouter 預設模型 + 降級鏈
# 用 "openrouter:auto" 觸發；限流時自動依序重試下一個
OPENROUTER_DEFAULT_CHAIN: list[str] = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]
OPENROUTER_AUTO_ALIAS = "auto"

# 429 重試策略
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BACKOFF_SECONDS = 3.0


# ─── Provider 判定 ─────────────────────────────────────────────

def _resolve_provider(model: str) -> tuple[str, str]:
    """
    回傳 (provider, normalized_model_name)
    provider ∈ {"ollama", "openrouter", "proxy"}
    """
    if model.startswith(OPENROUTER_PREFIX):
        return "openrouter", model[len(OPENROUTER_PREFIX):]
    if model.startswith(PROXY_PREFIX):
        return "proxy", model[len(PROXY_PREFIX):]

    forced = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if forced == "openrouter":
        return "openrouter", model
    if forced == "proxy":
        return "proxy", model
    if forced == "ollama":
        return "ollama", model

    # 啟發式：OpenRouter 模型名格式固定為 "org/model"，Ollama tag 用 ":"
    if "/" in model and ":" not in model.split("/")[-1]:
        return "openrouter", model

    return "ollama", model


# ─── OpenRouter Key 載入 ───────────────────────────────────────

_openrouter_key_cache: str | None = None


def _load_openrouter_key() -> str:
    global _openrouter_key_cache
    if _openrouter_key_cache:
        return _openrouter_key_cache

    key = (
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("OPENROUTER_KEY")
        or ""
    ).strip()

    if not key:
        key_file = _REPO_ROOT / "openrouter-key"
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()

    if not key:
        raise RuntimeError(
            "OpenRouter API key 未設定。請設 OPENROUTER_API_KEY 環境變數，"
            f"或把 key 寫入 {_REPO_ROOT / 'openrouter-key'}"
        )

    _openrouter_key_cache = key
    return key


# ─── 後端實作 ──────────────────────────────────────────────────

_ollama_mod = None
_openrouter_client = None
_proxy_client = None
_proxy_key_cache: str | None = None


def _get_ollama():
    global _ollama_mod
    if _ollama_mod is None:
        import ollama
        _ollama_mod = ollama
    return _ollama_mod


def _load_proxy_key() -> str:
    """Proxy API key：env PROXY_API_KEY → ANTHROPIC_API_KEY → 檔案 ANTHROPIC_API_KEY。"""
    global _proxy_key_cache
    if _proxy_key_cache:
        return _proxy_key_cache

    key = (
        os.environ.get("PROXY_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or ""
    ).strip()

    if not key:
        key_file_name = os.environ.get("PROXY_KEY_FILE", PROXY_KEY_FILE_DEFAULT)
        key_file = _REPO_ROOT / key_file_name
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()

    if not key:
        raise RuntimeError(
            "Proxy API key 未設定。請設 PROXY_API_KEY 或 ANTHROPIC_API_KEY 環境變數，"
            f"或把 key 寫入 {_REPO_ROOT / PROXY_KEY_FILE_DEFAULT}"
        )

    _proxy_key_cache = key
    return key


def _get_proxy_client():
    global _proxy_client
    if _proxy_client is None:
        from openai import OpenAI
        _proxy_client = OpenAI(
            base_url=os.environ.get("PROXY_BASE_URL", PROXY_BASE_URL_DEFAULT),
            api_key=_load_proxy_key(),
        )
    return _proxy_client


def _proxy_chat(model: str, messages: list[dict], temperature: float, stream: bool):
    """透過 OpenAI 相容反代呼叫（aiclient-2-api 之類）。"""
    client = _get_proxy_client()
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        stream=stream,
    )


def _get_openrouter_client():
    global _openrouter_client
    if _openrouter_client is None:
        from openai import OpenAI
        _openrouter_client = OpenAI(
            base_url=os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL_DEFAULT),
            api_key=_load_openrouter_key(),
        )
    return _openrouter_client


def _ollama_chat(model: str, messages: list[dict], temperature: float,
                 think: bool, stream: bool):
    client = _get_ollama()
    return client.chat(
        model=model,
        messages=messages,
        stream=stream,
        think=think,
        options={"temperature": temperature},
    )


def _openrouter_chat(model: str, messages: list[dict], temperature: float,
                     stream: bool):
    client = _get_openrouter_client()
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        stream=stream,
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    """判斷是否 429 / upstream 限流（涵蓋 openai SDK 與 provider-wrapped 錯誤）。"""
    try:
        from openai import RateLimitError
        if isinstance(exc, RateLimitError):
            return True
    except ImportError:
        pass
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code in (429, "429"):
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate-limit" in msg or "rate limit" in msg


def _resolve_openrouter_chain(model_name: str) -> list[str]:
    """
    把 model_name 解析成要嘗試的模型清單。

    - "auto"              → OPENROUTER_DEFAULT_CHAIN
    - "a,b,c"（逗號分隔）→ [a, b, c]（手動鏈）
    - 其他               → [model_name]（單一模型）
    """
    if model_name == OPENROUTER_AUTO_ALIAS:
        return list(OPENROUTER_DEFAULT_CHAIN)
    if "," in model_name:
        return [s.strip() for s in model_name.split(",") if s.strip()]
    return [model_name]


def _openrouter_call_with_fallback(
    model_chain: list[str], messages: list[dict],
    temperature: float, stream: bool,
):
    """
    依序嘗試 model_chain 每個模型；每個模型 429 會重試 N 次（指數退避），
    仍失敗則換下一個。非 429 錯誤直接往上拋。
    """
    last_err: Exception | None = None
    for idx, model in enumerate(model_chain):
        for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
            try:
                result = _openrouter_chat(model, messages, temperature, stream)
                if idx > 0 or attempt > 1:
                    print(f"  [llm_client] ↻ 使用 {model}"
                          f"{' (重試後成功)' if attempt > 1 else ''}")
                return result
            except Exception as e:
                last_err = e
                if _is_rate_limit_error(e) and attempt < RATE_LIMIT_MAX_RETRIES:
                    wait = RATE_LIMIT_BACKOFF_SECONDS * attempt
                    print(f"  [llm_client] {model} 429，等 {wait:.0f}s 重試 "
                          f"({attempt}/{RATE_LIMIT_MAX_RETRIES})")
                    time.sleep(wait)
                    continue
                break  # 非限流錯誤或重試耗盡 → 換下一個模型
        if _is_rate_limit_error(last_err) and idx < len(model_chain) - 1:
            nxt = model_chain[idx + 1]
            print(f"  [llm_client] ⤳ {model} 限流，降級至 {nxt}")
            continue
        # 最後一個模型也失敗 → 拋出
        raise last_err  # type: ignore[misc]
    # 不應該到這（迴圈會先 raise）
    raise last_err if last_err else RuntimeError("OpenRouter 呼叫鏈為空")


# ─── 公開 API ──────────────────────────────────────────────────

def chat_text(
    model: str,
    messages: list[dict],
    temperature: float = 0.0,
    think: bool = False,
) -> str:
    """
    單次呼叫，回傳整段文字。統一封裝 Ollama / OpenRouter。

    `think` 參數僅 Ollama 有效（關閉 reasoning tokens，節省輸出）。
    """
    provider, model_name = _resolve_provider(model)

    if provider == "openrouter":
        chain = _resolve_openrouter_chain(model_name)
        resp = _openrouter_call_with_fallback(chain, messages, temperature, stream=False)
        return resp.choices[0].message.content or ""

    if provider == "proxy":
        resp = _proxy_chat(model_name, messages, temperature, stream=False)
        return resp.choices[0].message.content or ""

    resp = _ollama_chat(model_name, messages, temperature, think=think, stream=False)
    return resp["message"]["content"]


def chat_stream(
    model: str,
    messages: list[dict],
    temperature: float = 0.0,
    think: bool = False,
) -> Iterator[str]:
    """
    串流呼叫，逐段 yield 文字片段。

    Ollama 回傳 chunk["message"]["content"]；
    OpenRouter 回傳 delta.content。
    """
    provider, model_name = _resolve_provider(model)

    if provider in ("openrouter", "proxy"):
        if provider == "openrouter":
            chain = _resolve_openrouter_chain(model_name)
            stream = _openrouter_call_with_fallback(chain, messages, temperature, stream=True)
        else:
            stream = _proxy_chat(model_name, messages, temperature, stream=True)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
        return

    stream = _ollama_chat(model_name, messages, temperature, think=think, stream=True)
    for chunk in stream:
        piece = chunk.get("message", {}).get("content", "")
        if piece:
            yield piece


def active_provider(model: str) -> str:
    """Utility：回傳此 model 會走哪個 provider（log 用）。"""
    return _resolve_provider(model)[0]


# ─── CLI ───────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="LLM Client 測試工具")
    p.add_argument("--model", required=True, help="模型名，如 gemma4:26b 或 openrouter:google/gemma-3-27b-it")
    p.add_argument("--prompt", required=True, help="測試 prompt")
    p.add_argument("--stream", action="store_true", help="串流輸出")
    args = p.parse_args()

    provider = active_provider(args.model)
    print(f"→ provider: {provider}\n")
    msgs = [{"role": "user", "content": args.prompt}]
    if args.stream:
        for piece in chat_stream(args.model, msgs, temperature=0):
            print(piece, end="", flush=True)
        print()
    else:
        print(chat_text(args.model, msgs, temperature=0))


if __name__ == "__main__":
    main()
