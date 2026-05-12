#!/usr/bin/env python3
"""
Memosyne — The Augury Replay (query_log.py)

Capture real queries（opt-in），讓 retrieval 改動有真實基準可比對。

設計原則：
    1. Off by default —— 必須設 MEMOSYNE_CAPTURE_QUERIES=1 才寫。
    2. Append-only NDJSON —— 與 chronicle.jsonl 同風格的可移植 source log。
    3. PII scrub —— 寫入前移除明顯的 email / phone / 強私有 token。
       不做語意級匿名化（v0.5 不展開）。
    4. 純函式 + 模組級狀態 —— 不依賴 chronicle.db / vectorize / tapestry，
       可單獨呼叫 / 單獨測試。

NDJSON schema："memosyne.query_log.v1"

    {
      "schema": "memosyne.query_log.v1",
      "ts": "2026-05-12T14:23:01.123456",
      "query": "redacted query text",
      "query_scrub_meta": {"emails": 0, "phones": 1},
      "retrieved_paths": ["30_Journal/.../foo.md", ...],
      "top_k": 5,
      "latency_ms": 124,
      "source": "search|mcp|cli|eval",
      "config_hash": "...",  // 之後可加，現在留空
    }

CLI：

    python3 query_log.py --stats
    python3 query_log.py --export --since 7d > baseline.jsonl
    python3 query_log.py --replay baseline.jsonl   # 留給 v0.5+1 實作
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

try:
    from artifacts import artifact_path, ensure_parent
except ImportError:
    def artifact_path(name: str) -> Path:
        mapping = {"query_log_jsonl": "query_log.jsonl"}
        return Path(__file__).parent / mapping.get(name, name)

    def ensure_parent(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)


QUERY_LOG_PATH = artifact_path("query_log_jsonl")
SCHEMA = "memosyne.query_log.v1"


# ─── Opt-in gate ─────────────────────────────────────────────

def is_capture_enabled() -> bool:
    """
    Capture 預設關閉。三種開啟方式（任一即可）：
        1. env: MEMOSYNE_CAPTURE_QUERIES=1
        2. env: MEMOSYNE_CAPTURE_QUERIES=true
        3. 程式內呼叫 set_capture_override(True)
    """
    raw = os.getenv("MEMOSYNE_CAPTURE_QUERIES", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    return _capture_override is True


_capture_override: bool | None = None


def set_capture_override(value: bool | None) -> None:
    """測試 / 程式化覆寫。None = 回到 env-only。"""
    global _capture_override
    _capture_override = value


# ─── PII scrub ───────────────────────────────────────────────
#
# v0.5 只做明顯 PII：email、phone、長 token 樣式。
# 不做姓名匿名（需私有清單，留給呼叫端透過 MEMOSYNE_QUERY_SCRUB_TERMS 提供）。

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b")
_PHONE_RE = re.compile(
    # 國際格式 / 台灣手機 / 一般有 dash 的數字串
    r"(?:\+\d{1,3}[\s-]?)?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}"
)
# 長隨機 token：>= 24 字母數字
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")


def _user_scrub_terms() -> list[str]:
    """從 env 讀使用者自訂禁字（逗號分隔）。"""
    raw = os.getenv("MEMOSYNE_QUERY_SCRUB_TERMS", "").strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def scrub(text: str) -> tuple[str, dict[str, int]]:
    """
    Redact PII from query text.

    Returns:
        (redacted_text, {field: count})
    """
    counts = {"emails": 0, "phones": 0, "tokens": 0, "custom": 0}

    def _sub_count(pattern: re.Pattern, key: str, repl: str, s: str) -> str:
        nonlocal counts
        matches = list(pattern.finditer(s))
        counts[key] = len(matches)
        return pattern.sub(repl, s)

    text = _sub_count(_EMAIL_RE, "emails", "<email>", text)
    text = _sub_count(_PHONE_RE, "phones", "<phone>", text)
    text = _sub_count(_TOKEN_RE, "tokens", "<token>", text)

    for term in _user_scrub_terms():
        if not term:
            continue
        before = text
        text = text.replace(term, "<redacted>")
        if text != before:
            counts["custom"] += 1

    # 移除空 count 讓 JSON 簡潔
    return text, {k: v for k, v in counts.items() if v > 0}


# ─── Capture ─────────────────────────────────────────────────

def record_query(
    query: str,
    retrieved_paths: list[str],
    *,
    top_k: int | None = None,
    latency_ms: int | None = None,
    source: str = "search",
    extra: dict | None = None,
) -> bool:
    """
    Append a query event to the log. No-op when capture is disabled.

    Returns:
        True if recorded; False if disabled or write failed.
    """
    if not is_capture_enabled():
        return False

    try:
        ensure_parent(QUERY_LOG_PATH)
        scrubbed, scrub_meta = scrub(query)
        event = {
            "schema": SCHEMA,
            "ts": datetime.now().isoformat(timespec="microseconds"),
            "query": scrubbed,
            "retrieved_paths": list(retrieved_paths)[:50],
            "top_k": top_k,
            "latency_ms": latency_ms,
            "source": source,
        }
        if scrub_meta:
            event["query_scrub_meta"] = scrub_meta
        if extra:
            # 不允許 extra 覆蓋核心 key
            for k, v in extra.items():
                if k not in event:
                    event[k] = v

        with QUERY_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except OSError:
        return False


# ─── Iter / export ───────────────────────────────────────────

def iter_events(
    since: datetime | None = None,
    sources: Iterable[str] | None = None,
    path: Path | None = None,
):
    """Yield events from the JSONL log, optionally filtered."""
    log_path = path or QUERY_LOG_PATH
    if not log_path.exists():
        return
    src_set = set(sources) if sources else None

    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("schema") != SCHEMA:
                continue
            if src_set is not None and ev.get("source") not in src_set:
                continue
            if since is not None:
                try:
                    ts = datetime.fromisoformat(ev.get("ts", ""))
                except (ValueError, TypeError):
                    continue
                if ts < since:
                    continue
            yield ev


def parse_since(spec: str) -> datetime:
    """
    Parse a relative duration like "7d", "24h", "30m", or an ISO date.
    Returns a datetime in the past.
    """
    spec = spec.strip()
    m = re.fullmatch(r"(\d+)([dhm])", spec)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        now = datetime.now()
        if unit == "d":
            return now - timedelta(days=n)
        if unit == "h":
            return now - timedelta(hours=n)
        if unit == "m":
            return now - timedelta(minutes=n)
    # fall back to ISO
    return datetime.fromisoformat(spec)


def export_ndjson(
    out_fh,
    *,
    since: datetime | None = None,
    sources: Iterable[str] | None = None,
) -> int:
    """Stream filtered events to a file handle as NDJSON. Returns count."""
    count = 0
    for ev in iter_events(since=since, sources=sources):
        out_fh.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
        count += 1
    return count


def stats() -> dict:
    """Aggregate stats over the full log."""
    total = 0
    by_source: dict[str, int] = {}
    latency_total = 0
    latency_count = 0
    first_ts: str | None = None
    last_ts: str | None = None

    for ev in iter_events():
        total += 1
        s = ev.get("source", "unknown")
        by_source[s] = by_source.get(s, 0) + 1
        lat = ev.get("latency_ms")
        if isinstance(lat, (int, float)) and lat >= 0:
            latency_total += int(lat)
            latency_count += 1
        ts = ev.get("ts")
        if isinstance(ts, str):
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts

    return {
        "total":          total,
        "by_source":      by_source,
        "mean_latency_ms": int(latency_total / latency_count) if latency_count else None,
        "first_ts":       first_ts,
        "last_ts":        last_ts,
        "log_path":       str(QUERY_LOG_PATH),
        "capture_enabled": is_capture_enabled(),
    }


# ─── Replay (skeleton — full implementation in v0.5+1) ───────

def replay(baseline_path: Path, top_k: int = 10) -> dict:
    """
    Re-run captured queries against current search and compute drift.

    Three metrics:
        mean_jaccard_at_k — average overlap of retrieved_paths sets
        top1_stability    — fraction of queries whose #1 result stayed
        latency_delta_ms  — mean (current − captured)

    NOTE (v0.5 status): The harness imports + structure is wired here,
    but full integration requires deciding whether to expose a clean
    `vectorize.search()` entry point that can be called from a test
    process with isolated artifacts. Tracked as a follow-up — call
    raises NotImplementedError so misuse fails fast.
    """
    raise NotImplementedError(
        "replay is wired but not yet executable; export baseline now, "
        "implement replay in v0.5 follow-up"
    )


# ─── CLI ─────────────────────────────────────────────────────

def _main() -> int:
    ap = argparse.ArgumentParser(description="Memosyne query log — The Augury Replay")
    ap.add_argument("--stats", action="store_true", help="aggregate stats over the log")
    ap.add_argument("--export", action="store_true",
                    help="emit filtered events as NDJSON to stdout")
    ap.add_argument("--since", default="",
                    help="filter: relative (7d / 24h / 30m) or ISO datetime")
    ap.add_argument("--source", default="",
                    help="filter: comma-separated source labels (search,mcp,...)")
    ap.add_argument("--replay", default="",
                    help="(stub) replay a previously exported baseline")
    args = ap.parse_args()

    if args.stats:
        s = stats()
        print(f"🜍 The Augury Replay — query log stats")
        print(f"   log path:          {s['log_path']}")
        print(f"   capture enabled:   {s['capture_enabled']}")
        print(f"   total events:      {s['total']}")
        print(f"   by source:         {s['by_source']}")
        print(f"   mean latency_ms:   {s['mean_latency_ms']}")
        print(f"   first event:       {s['first_ts']}")
        print(f"   last event:        {s['last_ts']}")
        return 0

    if args.export:
        since = parse_since(args.since) if args.since else None
        sources = [s.strip() for s in args.source.split(",") if s.strip()] or None
        n = export_ndjson(sys.stdout, since=since, sources=sources)
        print(f"(exported {n} events)", file=sys.stderr)
        return 0

    if args.replay:
        try:
            replay(Path(args.replay))
        except NotImplementedError as e:
            print(f"[replay] {e}", file=sys.stderr)
            return 2

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
