#!/usr/bin/env python3
"""
The Invocation — 繆思路由器（Muse Router）
=============================================

為 query 挑選最相關的 1–3 位繆思女神，把她們的領域記憶加權，
引導檢索走向最有可能藏著答案的記憶角落。

九位繆思的領域定義（結合 CLAUDE.md 神話對映與實際資料結構）：
    Clio        歷史            → 30_Journal 較早期 / 含歷史時期詞
    Thalia      日常/喜劇       → 30_Journal 日常隨筆
    Calliope    史詩/對話       → 20_AI_Chats
    Urania      天文/知識       → 50_Knowledge
    Polyhymnia  神聖詩歌/身份   → 10_Profile
    Erato       愛情詩          → emotions/themes 含情感關係語義
    Melpomene   悲劇            → emotions 偏負向（低潮、焦慮、悲傷）
    Terpsichore 舞蹈/行動       → 40_Projects
    Euterpe     音樂/創作       → themes 含創作/靈感/藝術語義

實作：
    1. 為每位繆思從既有記憶庫挑選 seed chunk → 平均 embedding → centroid
    2. query embedding → 9 centroids cosine → top-K 繆思
    3. 兩種模式：
         - soft weight：命中繆思領域的記憶 score × 1.3
         - hard filter：只保留命中繆思領域的記憶
    4. centroid 快取在 muse_centroids.json

用法：
    from muses import route, muse_boost_factor, muse_matches

    # query → 最相關繆思
    top_muses = route("我去年跟 friend-A 在 Tokyo 做了什麼 project")
    # → [("Terpsichore", 0.82), ("Clio", 0.71), ...]
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

CENTROIDS_PATH = Path(__file__).parent / "muse_centroids.json"

# ─── 繆思定義 ─────────────────────────────────────────────────
#
# 每位繆思定義：(希臘名, 領域描述, seed_selector)
# seed_selector(meta: dict) -> bool  判斷該 chunk 是否屬於此繆思領域
#
# meta 欄位（來自 Chroma metadata / BM25 metas）：
#   path, type, themes (csv str), emotions (csv str), period, locations, ...

_LOVE_WORDS    = ("愛", "戀", "曖昧", "感情", "關係", "伴侶", "女友", "男友", "喜歡", "告白")
_NEGATIVE_EMO  = ("低潮", "焦慮", "悲傷", "難過", "挫折", "失落", "痛苦", "憤怒",
                  "sad", "anxious", "frustrated", "depressed", "grief")
_CREATIVE_WORD = ("創作", "靈感", "音樂", "藝術", "作品", "寫作", "設計", "畫",
                  "poetry", "music", "creative", "art")
_HISTORY_WORD  = ("歷史", "回憶", "當年", "小時候", "過去", "年少", "舊事")


def _csv_to_list(val) -> list[str]:
    if not val:
        return []
    if isinstance(val, list):
        return [str(x) for x in val]
    return [s.strip() for s in str(val).split(",") if s.strip()]


def _any_hit(values: list[str], keywords: tuple[str, ...]) -> bool:
    joined = " ".join(values).lower()
    return any(kw.lower() in joined for kw in keywords)


def _is_clio(m: dict) -> bool:
    path = str(m.get("path", ""))
    if not path.startswith("30_Journal"):
        return False
    period = str(m.get("period", ""))
    themes = _csv_to_list(m.get("themes"))
    # 歷史感：有 period 標記、或主題含回憶/歷史、或日期年份 <= 2023
    if period:
        return True
    if _any_hit(themes, _HISTORY_WORD):
        return True
    date = str(m.get("date", ""))
    if len(date) >= 4 and date[:4].isdigit() and int(date[:4]) <= 2023:
        return True
    return False


def _is_thalia(m: dict) -> bool:
    path = str(m.get("path", ""))
    if not path.startswith("30_Journal"):
        return False
    # Thalia = 30_Journal 中「非 Clio」的日常部分
    return not _is_clio(m)


def _is_calliope(m: dict) -> bool:
    return str(m.get("path", "")).startswith("20_AI_Chats") or m.get("type") == "chat"


def _is_urania(m: dict) -> bool:
    return str(m.get("path", "")).startswith("50_Knowledge")


def _is_polyhymnia(m: dict) -> bool:
    return str(m.get("path", "")).startswith("10_Profile") or m.get("type") == "bio"


def _is_terpsichore(m: dict) -> bool:
    return str(m.get("path", "")).startswith("40_Projects")


def _is_erato(m: dict) -> bool:
    themes   = _csv_to_list(m.get("themes"))
    emotions = _csv_to_list(m.get("emotions"))
    return _any_hit(themes + emotions, _LOVE_WORDS)


def _is_melpomene(m: dict) -> bool:
    emotions = _csv_to_list(m.get("emotions"))
    themes   = _csv_to_list(m.get("themes"))
    return _any_hit(emotions + themes, _NEGATIVE_EMO)


def _is_euterpe(m: dict) -> bool:
    themes = _csv_to_list(m.get("themes"))
    return _any_hit(themes, _CREATIVE_WORD)


MUSES: dict[str, dict] = {
    "Clio":        {"domain": "Keeper of History",      "match": _is_clio},
    "Thalia":      {"domain": "Voice of Daily Life",    "match": _is_thalia},
    "Calliope":    {"domain": "Weaver of Conversations","match": _is_calliope},
    "Urania":      {"domain": "Guardian of Wisdom",     "match": _is_urania},
    "Polyhymnia":  {"domain": "Singer of Identity",     "match": _is_polyhymnia},
    "Erato":       {"domain": "Muse of Love",           "match": _is_erato},
    "Melpomene":   {"domain": "Muse of Sorrow",         "match": _is_melpomene},
    "Terpsichore": {"domain": "Muse of Action",         "match": _is_terpsichore},
    "Euterpe":     {"domain": "Muse of Creation",       "match": _is_euterpe},
}


def muse_matches(meta: dict, muse: str) -> bool:
    """判斷 meta 是否屬於指定繆思領域。"""
    m = MUSES.get(muse)
    if not m:
        return False
    try:
        return bool(m["match"](meta))
    except Exception:
        return False


# ─── Centroid 計算 ────────────────────────────────────────────

def build_centroids(min_seeds: int = 3, verbose: bool = True) -> dict[str, list[float]]:
    """
    從 ChromaDB 為每位繆思建立 centroid embedding。

    步驟：
      1. 讀取所有 chunks 的 (embedding, metadata)
      2. 對每位繆思，挑出 match(meta) 為 True 的 chunks
      3. 平均 embeddings → centroid
      4. seed 不足 min_seeds 的繆思，centroid 設為 None
      5. 持久化到 muse_centroids.json

    Returns:
        dict[muse_name, centroid_vector or None]
    """
    import numpy as np
    from vectorize import get_collection

    _, col = get_collection()
    raw = col.get(include=["embeddings", "metadatas"])
    ids   = raw.get("ids", [])
    embs  = raw.get("embeddings", [])
    metas = raw.get("metadatas", [])

    if not ids:
        raise RuntimeError("ChromaDB 為空，無法建立 centroid — 請先 build_index")

    centroids: dict[str, list[float] | None] = {}
    counts:    dict[str, int] = {}

    for muse, spec in MUSES.items():
        match = spec["match"]
        vecs = [e for e, m in zip(embs, metas) if match(m)]
        counts[muse] = len(vecs)
        if len(vecs) < min_seeds:
            centroids[muse] = None
            if verbose:
                print(f"  ⚠️  {muse:12s} seeds={len(vecs)} (< {min_seeds}) — centroid 略過")
            continue
        arr = np.asarray(vecs, dtype=np.float32)
        centroid = arr.mean(axis=0)
        # L2 normalize for cosine similarity
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        centroids[muse] = centroid.tolist()
        if verbose:
            print(f"  🎭 {muse:12s} seeds={len(vecs):4d}  ({spec['domain']})")

    payload = {
        "embed_model": "paraphrase-multilingual-MiniLM-L12-v2",
        "counts": counts,
        "centroids": centroids,
    }
    CENTROIDS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if verbose:
        print(f"\n  已寫入 {CENTROIDS_PATH.name}")
    return centroids


# ─── Routing ──────────────────────────────────────────────────

_centroid_cache: dict | None = None


def _load_centroids() -> dict[str, list[float] | None]:
    global _centroid_cache
    if _centroid_cache is not None:
        return _centroid_cache
    if not CENTROIDS_PATH.exists():
        _centroid_cache = {}
        return _centroid_cache
    try:
        data = json.loads(CENTROIDS_PATH.read_text(encoding="utf-8"))
        _centroid_cache = data.get("centroids", {}) or {}
    except (json.JSONDecodeError, OSError):
        _centroid_cache = {}
    return _centroid_cache


def _embed(text: str) -> list[float]:
    """計算單一 query 的 embedding（沿用 vectorize 的模型）。"""
    from model_env import configure_hf_runtime
    configure_hf_runtime()
    from sentence_transformers import SentenceTransformer
    from vectorize import EMBED_MODEL
    # cache model instance
    global _embed_model
    try:
        model = _embed_model  # type: ignore[name-defined]
    except NameError:
        model = SentenceTransformer(EMBED_MODEL)
        globals()["_embed_model"] = model
    import numpy as np
    v = model.encode([text], normalize_embeddings=True)[0]
    return np.asarray(v, dtype=np.float32).tolist()


def route(query: str, top_k: int = 3, threshold: float = 0.15) -> list[tuple[str, float]]:
    """
    根據 query 選出最相關的繆思。

    Args:
        query: 使用者問題
        top_k: 最多回傳幾位繆思
        threshold: 最低餘弦相似度，過低視為無關

    Returns:
        [(muse_name, score), ...] 由高到低，至多 top_k 位
        若找不到 centroid 或全部低於 threshold → 回傳 []
    """
    import numpy as np

    centroids = _load_centroids()
    if not centroids:
        return []

    qv = np.asarray(_embed(query), dtype=np.float32)
    qn = np.linalg.norm(qv)
    if qn == 0:
        return []
    qv = qv / qn

    scored: list[tuple[str, float]] = []
    for muse, cv in centroids.items():
        if cv is None:
            continue
        v = np.asarray(cv, dtype=np.float32)
        # centroids 已經 normalized；點積 = cosine
        score = float(np.dot(qv, v))
        if score >= threshold:
            scored.append((muse, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def muse_boost_factor(meta: dict, muses: list[str], boost: float = 1.3) -> float:
    """
    若 meta 屬於任一命中的繆思領域，回傳 boost；否則 1.0。
    用於 soft weight 模式：result["score"] *= muse_boost_factor(meta, muses)
    """
    if not muses:
        return 1.0
    for muse in muses:
        if muse_matches(meta, muse):
            return boost
    return 1.0


def muse_boost_factor_confidence(
    meta: dict,
    muse_scores: dict[str, float],
    threshold: float = 0.20,
    k: float = 2.0,
    max_boost: float = 1.5,
) -> float:
    """
    Confidence-scaled boost：boost 強度隨 router 對該繆思的信心線性成長。

    公式：boost = 1 + (router_score - threshold) × k，clamp 至 [1.0, max_boost]
      - router_score ≤ threshold → boost = 1.0（不加分）
      - router_score = threshold + 0.10, k=2 → boost = 1.20
      - router_score ≥ threshold + (max_boost-1)/k → boost = max_boost（封頂）

    若 meta 匹配多位繆思，取最高 boost。
    """
    if not muse_scores:
        return 1.0
    best = 1.0
    for muse, score in muse_scores.items():
        if not muse_matches(meta, muse):
            continue
        b = 1.0 + max(0.0, score - threshold) * k
        if b > max_boost:
            b = max_boost
        if b > best:
            best = b
    return best


def muse_penalty_factor_confidence(
    meta: dict,
    muse_scores: dict[str, float],
    threshold: float = 0.20,
    k: float = 0.5,
    min_factor: float = 0.85,
) -> float:
    """
    Soft penalty：只對非命中繆思按 router 信心扣分，命中者維持 ×1.0。

    公式（非命中）：factor = 1 - (max_router_score - threshold) × k，clamp 至 [min_factor, 1.0]
      - router 信心低（score ≤ threshold）→ factor = 1.0（不扣分）
      - router 信心高 → factor 接近 min_factor（最多扣 15%）

    相較於 boost 版，避免「命中者被抬到天花板、其他人全被壓出 top-10」的副作用。
    """
    if not muse_scores:
        return 1.0
    if any(muse_matches(meta, m) for m in muse_scores):
        return 1.0
    max_score = max(muse_scores.values())
    factor = 1.0 - max(0.0, max_score - threshold) * k
    if factor < min_factor:
        factor = min_factor
    return factor


def filter_by_muses(results: list[dict], muses: list[str]) -> list[dict]:
    """Hard filter：只保留屬於指定繆思之一的結果。"""
    if not muses:
        return results
    out = []
    for r in results:
        for muse in muses:
            if muse_matches(r, muse):
                out.append(r)
                break
    return out


# ─── CLI ──────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="The Invocation — 繆思路由器")
    p.add_argument("--build",   action="store_true", help="建立 / 更新 centroid 快取")
    p.add_argument("--route",   type=str, default="", help="測試：query → top 繆思")
    p.add_argument("--stats",   action="store_true", help="顯示 centroid 統計")
    p.add_argument("--min-seeds", type=int, default=3, help="繆思至少需要 N 個 seed")
    args = p.parse_args()

    if args.build:
        print("🎭 The Invocation — 召喚九位繆思...")
        build_centroids(min_seeds=args.min_seeds)
        return

    if args.stats:
        if not CENTROIDS_PATH.exists():
            print("尚未建立 centroid，請先執行 --build")
            return
        data = json.loads(CENTROIDS_PATH.read_text(encoding="utf-8"))
        print(f"Embed model : {data.get('embed_model')}")
        print(f"Centroid 檔 : {CENTROIDS_PATH.name}\n")
        for muse, cnt in (data.get("counts") or {}).items():
            c = data["centroids"].get(muse)
            status = "✓" if c else "—"
            print(f"  [{status}] {muse:12s} seeds={cnt:4d}  {MUSES[muse]['domain']}")
        return

    if args.route:
        top = route(args.route, top_k=5, threshold=0.0)
        print(f"Query: {args.route}\n")
        if not top:
            print("  沒有繆思回應。centroids 尚未建立？")
            return
        for muse, score in top:
            print(f"  {score:+.4f}  {muse:12s} {MUSES[muse]['domain']}")
        return

    p.print_help()


if __name__ == "__main__":
    main()
