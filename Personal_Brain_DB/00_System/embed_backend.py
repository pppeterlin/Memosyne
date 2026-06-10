#!/usr/bin/env python3
"""
Pluggable embedding backend for Memosyne (v0.8).

Embedding 是整個檢索流程裡最吃 CPU/GPU 的批次苦力活。v0.8 把它抽成可插拔
後端,讓使用者在 config 裡選擇要本地跑、丟給自有 GPU server(Ollama),
還是走公有雲(OpenAI-compatible /v1/embeddings)。

設定（env / .env）:
    MEMOSYNE_EMBED_PROVIDER = local | ollama | openai-compat   (預設 local)
    MEMOSYNE_EMBED_URL      = http://gpu-host:11434            (remote 必填)
    MEMOSYNE_EMBED_MODEL    = nomic-embed-text                 (remote 必填)
    MEMOSYNE_EMBED_DIM      = 768                              (選填,顯式宣告維度防 silent mismatch)
    MEMOSYNE_EMBED_API_KEY  = sk-...                           (公有雲 openai-compat 用)
    MEMOSYNE_EMBED_TIMEOUT  = 30                               (選填,單次請求秒數)
    MEMOSYNE_EMBED_RETRIES  = 3                                (選填,失敗重試次數)

設計原則:
- 預設 local → 與 v0.7 完全一致,不設 env 的人完全無感(向後相容)。
- 失敗顯式 → remote 連不通直接 raise,絕不靜默降級回本地(否則 batch 會
  突然慢 50× 而你找不到原因)。
- 維度即契約 → 切換 backend 必須用同一個 embedding model;dim 不符就報錯。
- batch → 一次 HTTP 包多段,避免每段一個 RTT。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# 本地預設 model(與 v0.7 一致)
LOCAL_DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

VALID_PROVIDERS = {"local", "ollama", "openai-compat"}


class EmbeddingBackendError(RuntimeError):
    """Remote endpoint 不通 / 維度不符 / 回應格式錯誤時拋出（fail-fast）。"""


@dataclass
class EmbedConfig:
    provider: str = "local"
    url: str = ""
    model: str = LOCAL_DEFAULT_MODEL
    dim: int | None = None
    api_key: str = ""
    timeout: float = 30.0
    retries: int = 3
    batch_size: int = 64

    @property
    def is_remote(self) -> bool:
        return self.provider in {"ollama", "openai-compat"}

    def describe(self) -> str:
        if self.provider == "local":
            return f"local sentence-transformers ({self.model})"
        return f"{self.provider} {self.model} @ {self.url}"


def resolve_config(env: dict | None = None) -> EmbedConfig:
    """從環境變數解析 embedding 設定。未設 provider → local（向後相容）。"""
    e = env if env is not None else os.environ
    provider = (e.get("MEMOSYNE_EMBED_PROVIDER") or "local").strip().lower()
    if provider not in VALID_PROVIDERS:
        raise EmbeddingBackendError(
            f"MEMOSYNE_EMBED_PROVIDER='{provider}' 無效。"
            f"可選: {', '.join(sorted(VALID_PROVIDERS))}"
        )

    model_default = LOCAL_DEFAULT_MODEL if provider == "local" else ""
    cfg = EmbedConfig(
        provider=provider,
        url=(e.get("MEMOSYNE_EMBED_URL") or "").strip().rstrip("/"),
        model=(e.get("MEMOSYNE_EMBED_MODEL") or model_default).strip(),
        api_key=(e.get("MEMOSYNE_EMBED_API_KEY") or "").strip(),
    )

    dim_raw = (e.get("MEMOSYNE_EMBED_DIM") or "").strip()
    if dim_raw:
        try:
            cfg.dim = int(dim_raw)
        except ValueError as exc:
            raise EmbeddingBackendError(
                f"MEMOSYNE_EMBED_DIM='{dim_raw}' 不是整數"
            ) from exc

    for key, attr, cast in (
        ("MEMOSYNE_EMBED_TIMEOUT", "timeout", float),
        ("MEMOSYNE_EMBED_RETRIES", "retries", int),
        ("MEMOSYNE_EMBED_BATCH", "batch_size", int),
    ):
        raw = (e.get(key) or "").strip()
        if raw:
            try:
                setattr(cfg, attr, cast(raw))
            except ValueError as exc:
                raise EmbeddingBackendError(f"{key}='{raw}' 格式錯誤") from exc

    if cfg.is_remote:
        if not cfg.url:
            raise EmbeddingBackendError(
                f"provider={provider} 需要 MEMOSYNE_EMBED_URL（remote endpoint）"
            )
        if not cfg.model:
            raise EmbeddingBackendError(
                f"provider={provider} 需要 MEMOSYNE_EMBED_MODEL"
            )
    return cfg


# ─── HTTP helper ─────────────────────────────────────────────

def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ping_endpoint(config: EmbedConfig, timeout: float = 3.0) -> tuple[bool, str]:
    """
    輕量 reachability 檢查（不做 embedding、不重試）—— 供 providers list / health 用。

    只確認 remote endpoint 活著，不耗 GPU：
      - ollama        → GET {url}/api/tags
      - openai-compat → GET {url}/v1/models

    回傳 (reachable, detail)。local provider 永遠 (True, describe)。
    """
    if not config.is_remote:
        return True, config.describe()
    path = "/api/tags" if config.provider == "ollama" else "/v1/models"
    url = f"{config.url}{path}"
    req = urllib.request.Request(url, method="GET")
    if config.api_key:
        req.add_header("Authorization", f"Bearer {config.api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ok = 200 <= r.status < 300
            return ok, f"{config.url} (HTTP {r.status})"
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        return False, f"{config.url} — {type(e).__name__}: {e}"


# ─── Remote embedding function (ChromaDB EmbeddingFunction 介面) ───

class RemoteEmbeddingFunction:
    """
    符合 chromadb EmbeddingFunction 協定: __call__(input: list[str]) -> list[list[float]]

    支援兩種 protocol:
      - ollama       : POST {url}/api/embed   {"model","input":[...]} -> {"embeddings":[[...]]}
      - openai-compat: POST {url}/v1/embeddings {"model","input":[...]} -> {"data":[{"embedding","index"}]}
    """

    def __init__(self, config: EmbedConfig):
        if not config.is_remote:
            raise EmbeddingBackendError(
                "RemoteEmbeddingFunction 只接受 ollama / openai-compat provider"
            )
        self.config = config

    # chromadb 會呼叫 ef.name() 做 collection metadata
    @staticmethod
    def name() -> str:
        return "memosyne-remote"

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002 (chroma 介面)
        if isinstance(input, str):
            input = [input]
        out: list[list[float]] = []
        bs = max(1, self.config.batch_size)
        for i in range(0, len(input), bs):
            batch = input[i : i + bs]
            out.extend(self._embed_batch(batch))
        return out

    # ── 批次請求 + 重試 ──
    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last_err: Exception | None = None
        for attempt in range(self.config.retries):
            try:
                vecs = self._call_endpoint(batch)
                self._check_dim(vecs)
                return vecs
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                if attempt < self.config.retries - 1:
                    time.sleep(0.5 * (2 ** attempt))  # 指數 backoff
                    continue
        raise EmbeddingBackendError(
            f"embedding endpoint 不通: {self.config.url} "
            f"({self.config.retries} 次重試後失敗) — {type(last_err).__name__}: {last_err}\n"
            f"  next: 1. tailscale status / ping {self.config.url}\n"
            f"        2. 確認遠端 ollama serve 在跑\n"
            f"        3. 或 unset MEMOSYNE_EMBED_PROVIDER 退回本地"
        ) from last_err

    def _call_endpoint(self, batch: list[str]) -> list[list[float]]:
        if self.config.provider == "ollama":
            return self._call_ollama(batch)
        return self._call_openai_compat(batch)

    def _call_ollama(self, batch: list[str]) -> list[list[float]]:
        url = f"{self.config.url}/api/embed"
        resp = _post_json(
            url,
            {"model": self.config.model, "input": batch},
            headers={},
            timeout=self.config.timeout,
        )
        vecs = resp.get("embeddings")
        if not isinstance(vecs, list) or len(vecs) != len(batch):
            raise EmbeddingBackendError(
                f"ollama 回應格式異常: 期望 {len(batch)} 個向量,得到 "
                f"{len(vecs) if isinstance(vecs, list) else type(vecs)}"
            )
        return vecs

    def _call_openai_compat(self, batch: list[str]) -> list[list[float]]:
        url = f"{self.config.url}/v1/embeddings"
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        resp = _post_json(
            url,
            {"model": self.config.model, "input": batch},
            headers=headers,
            timeout=self.config.timeout,
        )
        data = resp.get("data")
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingBackendError(
                f"openai-compat 回應格式異常: 期望 {len(batch)} 個向量,得到 "
                f"{len(data) if isinstance(data, list) else type(data)}"
            )
        # 依 index 排序,確保順序與輸入一致
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in ordered]

    def _check_dim(self, vecs: list[list[float]]) -> None:
        if not vecs:
            return
        got = len(vecs[0])
        if self.config.dim is not None and got != self.config.dim:
            raise EmbeddingBackendError(
                f"維度不符: endpoint 回傳 dim={got},但 MEMOSYNE_EMBED_DIM={self.config.dim}\n"
                f"  → 換了 embedding model 就要重建索引: memosyne embed migrate"
            )

    def probe(self) -> tuple[int, int]:
        """跑一次 hello-world embed,回傳 (dim, latency_ms)。供 health / providers test 用。"""
        t0 = time.time()
        vec = self._embed_batch(["hello"])[0]
        return len(vec), int((time.time() - t0) * 1000)


# ─── 工廠：給 vectorize.get_collection 用 ─────────────────────

def make_embedding_function(config: EmbedConfig | None = None):
    """
    回傳一個 chromadb 可用的 embedding function。

    - provider=local  → SentenceTransformerEmbeddingFunction（與 v0.7 一致）
    - provider=remote → RemoteEmbeddingFunction
    """
    cfg = config or resolve_config()
    if cfg.provider == "local":
        from chromadb.utils import embedding_functions
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=cfg.model
        )
    return RemoteEmbeddingFunction(cfg)
