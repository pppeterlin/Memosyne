#!/usr/bin/env python3
"""
The Chronicle of Mneme — 記憶存取紀錄與認知衰減重排

基於 ACT-R（Adaptive Control of Thought—Rational）認知架構：
記憶的「激活強度」取決於使用頻率與時間距離。

    A_i = ln( Σ_{k=1}^{n} t_k^{-d} )

    n  = 該記憶被存取的次數
    t_k = 第 k 次存取距今的時間（小時）
    d  = 衰減參數（預設 0.5）

近期且頻繁使用的記憶，激活分數越高。
從未被存取的記憶激活分數為 0（不懲罰，只是不加分）。

用途：
  - 搜尋結果 reranking（RRF 分數 + ACT-R bonus）
  - 未來 Strategic Forgetting 的依據
  - JSONL append-only artifact 作為可移植 source log

儲存策略：
  - chronicle.jsonl：The Chronicle 的事件真相，逐行追加
  - chronicle.db：SQLite 查詢快取，可從 JSONL 重建

執行方式：
  python3 mneme_weight.py --stats            # 存取紀錄統計
  python3 mneme_weight.py --top 10           # 最活躍的 10 個記憶
  python3 mneme_weight.py --score "path"     # 查詢特定記憶的激活分數
  python3 mneme_weight.py --export-jsonl --replace-jsonl
  python3 mneme_weight.py --rebuild-db-from-jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

try:
    from artifacts import artifact_path, ensure_parent
except ImportError:
    def artifact_path(name: str) -> Path:
        mapping = {
            "chronicle_db": "chronicle.db",
            "chronicle_jsonl": "chronicle.jsonl",
        }
        return Path(__file__).parent / mapping.get(name, name)

    def ensure_parent(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

CHRONICLE_DB = artifact_path("chronicle_db")
CHRONICLE_JSONL = artifact_path("chronicle_jsonl")
CHRONICLE_SCHEMA = "memosyne.chronicle.access.v1"

# ACT-R 預設衰減參數
DECAY_D = 0.5

# rerank 時 ACT-R 分數的權重係數
ACTR_ALPHA = 0.2


# ─── 資料庫初始化 ───────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """取得 Chronicle 資料庫連線，自動建表。"""
    ensure_parent(CHRONICLE_DB)
    conn = sqlite3.connect(str(CHRONICLE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS access_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_path TEXT    NOT NULL,
            accessed_at TEXT    NOT NULL,
            source      TEXT    DEFAULT 'search'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_access_path
        ON access_events(memory_path)
    """)
    conn.commit()
    return conn


# ─── 記錄存取 ──────────────────────────────────────────────

def _append_jsonl_events(memory_paths: list[str], source: str, accessed_at: str) -> int:
    """
    Append Chronicle access events to JSONL.

    JSONL is the portable source log. SQLite remains a derived acceleration cache
    for ACT-R scoring and statistics.
    """
    if not memory_paths:
        return 0

    ensure_parent(CHRONICLE_JSONL)
    with CHRONICLE_JSONL.open("a", encoding="utf-8") as fh:
        for path in memory_paths:
            event = {
                "schema": CHRONICLE_SCHEMA,
                "accessed_at": accessed_at,
                "memory_path": path,
                "source": source,
            }
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return len(memory_paths)


def iter_jsonl_events(path: Path | None = None):
    """Yield valid Chronicle events from a JSONL file, skipping malformed lines."""
    jsonl_path = path or CHRONICLE_JSONL
    if not jsonl_path.exists():
        return
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"The Chronicle skipped malformed JSONL line {line_no}.")
                continue
            if not isinstance(event, dict):
                continue
            memory_path = event.get("memory_path")
            accessed_at = event.get("accessed_at")
            if not memory_path or not accessed_at:
                continue
            yield {
                "memory_path": str(memory_path),
                "accessed_at": str(accessed_at),
                "source": str(event.get("source") or "search"),
            }

def record_access(memory_paths: list[str], source: str = "search") -> None:
    """
    記錄一批記憶被存取。

    Args:
        memory_paths: 被存取的記憶路徑列表
        source: 存取來源（search / mcp_search / mcp_read）
    """
    if not memory_paths:
        return
    now = datetime.now().isoformat()
    conn = get_db()
    conn.executemany(
        "INSERT INTO access_events (memory_path, accessed_at, source) VALUES (?, ?, ?)",
        [(p, now, source) for p in memory_paths],
    )
    conn.commit()
    conn.close()
    try:
        _append_jsonl_events(memory_paths, source=source, accessed_at=now)
    except OSError as e:
        print(f"The Chronicle JSONL faltered: {e}")


# ─── ACT-R 激活分數計算 ────────────────────────────────────

def compute_activation(memory_path: str, conn: sqlite3.Connection | None = None,
                       now: datetime | None = None, d: float = DECAY_D) -> float:
    """
    計算單一記憶的 ACT-R 基礎激活分數。

    A_i = ln( Σ_{k=1}^{n} t_k^{-d} )

    Returns:
        激活分數（float），無存取紀錄時回傳 0.0
    """
    close_conn = False
    if conn is None:
        if not CHRONICLE_DB.exists():
            return 0.0
        conn = get_db()
        close_conn = True

    if now is None:
        now = datetime.now()

    rows = conn.execute(
        "SELECT accessed_at FROM access_events WHERE memory_path = ?",
        (memory_path,),
    ).fetchall()

    if close_conn:
        conn.close()

    if not rows:
        return 0.0

    total = 0.0
    for (accessed_at_str,) in rows:
        try:
            accessed_at = datetime.fromisoformat(accessed_at_str)
        except (ValueError, TypeError):
            continue
        delta_hours = max((now - accessed_at).total_seconds() / 3600, 0.01)
        total += delta_hours ** (-d)

    if total <= 0:
        return 0.0
    return math.log(total)


def compute_activations_batch(memory_paths: list[str]) -> dict[str, float]:
    """
    批次計算多個記憶的 ACT-R 分數。

    Returns:
        {memory_path: activation_score}
    """
    if not CHRONICLE_DB.exists():
        return {p: 0.0 for p in memory_paths}

    conn = get_db()
    now = datetime.now()
    result = {}
    for path in memory_paths:
        result[path] = compute_activation(path, conn=conn, now=now)
    conn.close()
    return result


# ─── Rerank ────────────────────────────────────────────────

def actr_rerank(results: list[dict], alpha: float = ACTR_ALPHA) -> list[dict]:
    """
    對搜尋結果施加 ACT-R 認知衰減重排。

    final_score = original_score + α × normalized_actr_score

    Args:
        results: 搜尋結果列表，每個 dict 需有 'path' 和 'score'
        alpha: ACT-R 分數的權重（預設 0.2）

    Returns:
        重排後的結果列表（score 已更新）
    """
    if not results:
        return results

    paths = [r["path"] for r in results]
    activations = compute_activations_batch(paths)

    # 找出最大激活分數做 normalization
    max_act = max(activations.values()) if activations else 0.0
    if max_act <= 0:
        return results  # 無存取紀錄，不改變排名

    # 複製結果，加入 ACT-R bonus
    reranked = []
    for r in results:
        new_r = dict(r)
        act_score = activations.get(r["path"], 0.0)
        normalized = act_score / max_act  # 0~1 之間
        new_r["actr_score"] = round(act_score, 4)
        new_r["score"] = round(r.get("score", 0.0) + alpha * normalized, 4)
        reranked.append(new_r)

    reranked.sort(key=lambda x: -x["score"])
    return reranked


# ─── JSONL / SQLite 同步 ────────────────────────────────────

def export_jsonl_from_db(replace: bool = False, path: Path | None = None) -> int:
    """
    Export existing SQLite Chronicle rows into JSONL.

    Args:
        replace: overwrite the target JSONL before export
        path: optional target path; defaults to CHRONICLE_JSONL
    """
    target = path or CHRONICLE_JSONL
    if not CHRONICLE_DB.exists():
        return 0

    ensure_parent(target)
    mode = "w" if replace else "a"
    conn = get_db()
    rows = conn.execute("""
        SELECT id, memory_path, accessed_at, source
        FROM access_events
        ORDER BY id ASC
    """).fetchall()
    conn.close()

    with target.open(mode, encoding="utf-8") as fh:
        for row_id, memory_path, accessed_at, source in rows:
            event = {
                "schema": CHRONICLE_SCHEMA,
                "db_id": row_id,
                "accessed_at": accessed_at,
                "memory_path": memory_path,
                "source": source or "search",
            }
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def rebuild_db_from_jsonl(path: Path | None = None) -> int:
    """
    Rebuild the SQLite acceleration cache from Chronicle JSONL.

    This intentionally clears access_events first so repeated rebuilds are
    deterministic and do not duplicate events.
    """
    source = path or CHRONICLE_JSONL
    conn = get_db()
    conn.execute("DELETE FROM access_events")

    rows = [
        (event["memory_path"], event["accessed_at"], event["source"])
        for event in iter_jsonl_events(source)
    ]
    if rows:
        conn.executemany(
            "INSERT INTO access_events (memory_path, accessed_at, source) VALUES (?, ?, ?)",
            rows,
        )
    conn.commit()
    conn.close()
    return len(rows)


def count_jsonl_events(path: Path | None = None) -> int:
    """Count valid Chronicle JSONL events."""
    return sum(1 for _ in iter_jsonl_events(path or CHRONICLE_JSONL))


# ─── 統計 ──────────────────────────────────────────────────

def chronicle_stats() -> dict:
    """回傳 Chronicle 統計資訊。"""
    if not CHRONICLE_DB.exists():
        return {
            "total_events": 0,
            "unique_memories": 0,
            "sources": {},
            "jsonl_events": count_jsonl_events(),
            "db_path": str(CHRONICLE_DB),
            "jsonl_path": str(CHRONICLE_JSONL),
        }

    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM access_events").fetchone()[0]
    unique = conn.execute("SELECT COUNT(DISTINCT memory_path) FROM access_events").fetchone()[0]
    sources = dict(conn.execute(
        "SELECT source, COUNT(*) FROM access_events GROUP BY source"
    ).fetchall())

    # 最活躍的記憶
    top = conn.execute("""
        SELECT memory_path, COUNT(*) as cnt
        FROM access_events
        GROUP BY memory_path
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()

    conn.close()
    return {
        "total_events": total,
        "unique_memories": unique,
        "sources": sources,
        "top_accessed": top,
        "jsonl_events": count_jsonl_events(),
        "db_path": str(CHRONICLE_DB),
        "jsonl_path": str(CHRONICLE_JSONL),
    }


# ─── CLI ───────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="The Chronicle of Mneme — ACT-R 認知衰減系統")
    ap.add_argument("--stats", action="store_true", help="顯示存取紀錄統計")
    ap.add_argument("--top", type=int, default=0, help="顯示最活躍的 N 個記憶")
    ap.add_argument("--score", type=str, default="", help="查詢特定記憶的激活分數")
    ap.add_argument("--export-jsonl", action="store_true",
                    help="將既有 SQLite Chronicle 匯出為 append-only JSONL")
    ap.add_argument("--replace-jsonl", action="store_true",
                    help="搭配 --export-jsonl 使用：覆寫既有 JSONL")
    ap.add_argument("--rebuild-db-from-jsonl", action="store_true",
                    help="從 Chronicle JSONL 重建 SQLite 快取")
    ap.add_argument("--jsonl-path", type=str, default="",
                    help="指定 JSONL 匯出/匯入路徑（預設為 _vault/chronicle.jsonl）")
    args = ap.parse_args()

    jsonl_path = Path(args.jsonl_path).expanduser() if args.jsonl_path else CHRONICLE_JSONL

    if args.export_jsonl:
        count = export_jsonl_from_db(replace=args.replace_jsonl, path=jsonl_path)
        mode = "rewritten" if args.replace_jsonl else "appended"
        print(f"📜 The Chronicle JSONL was {mode}: {count} events → {jsonl_path}")
        return

    if args.rebuild_db_from_jsonl:
        count = rebuild_db_from_jsonl(path=jsonl_path)
        print(f"📜 SQLite Chronicle cache rebuilt from JSONL: {count} events → {CHRONICLE_DB}")
        return

    if args.stats:
        stats = chronicle_stats()
        print(f"📜 The Chronicle of Mneme")
        print(f"   總存取次數：{stats['total_events']}")
        print(f"   JSONL 事件數：{stats['jsonl_events']}")
        print(f"   已觸碰記憶：{stats['unique_memories']}")
        print(f"   存取來源：{stats['sources']}")
        print(f"   SQLite：{stats['db_path']}")
        print(f"   JSONL：{stats['jsonl_path']}")
        if stats.get("top_accessed"):
            print(f"\n   最活躍記憶：")
            for path, cnt in stats["top_accessed"]:
                score = compute_activation(path)
                print(f"     [{cnt:3d} 次] ACT-R={score:+.3f}  {path}")
        return

    if args.top:
        if not CHRONICLE_DB.exists():
            print("The Chronicle is empty. No memories have been touched yet.")
            return
        conn = get_db()
        rows = conn.execute("""
            SELECT memory_path, COUNT(*) as cnt
            FROM access_events
            GROUP BY memory_path
            ORDER BY cnt DESC
            LIMIT ?
        """, (args.top,)).fetchall()
        conn.close()
        now = datetime.now()
        print(f"📜 Top {args.top} most active memories:\n")
        for path, cnt in rows:
            score = compute_activation(path)
            print(f"  ACT-R={score:+.3f}  [{cnt:3d} accesses]  {path}")
        return

    if args.score:
        score = compute_activation(args.score)
        print(f"ACT-R activation for '{args.score}': {score:+.4f}")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
