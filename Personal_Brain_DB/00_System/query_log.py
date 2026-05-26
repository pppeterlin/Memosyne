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
    python3 query_log.py --replay baseline.jsonl --top-k 10
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


# ─── Replay ──────────────────────────────────────────────────
#
# 比對單位是 path（檔案層級），不是 chunk_id。
# 理由：chunk_id 是內部記帳細節（v0.6 可能改命名），path 是使用者真正在意的
# 「找到了哪份記憶」。把比對鎖在 path 層讓 replay 可以跨內部重構穩定。

def _load_baseline(baseline_path: Path) -> list[dict]:
    """Read a previously exported baseline NDJSON. Only well-formed events kept."""
    if not baseline_path.exists():
        raise FileNotFoundError(f"baseline not found: {baseline_path}")
    events: list[dict] = []
    with baseline_path.open("r", encoding="utf-8") as fh:
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
            if not ev.get("query") or not isinstance(ev.get("retrieved_paths"), list):
                continue
            events.append(ev)
    return events


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def replay(
    baseline_path: Path,
    *,
    top_k: int = 10,
    top_n_regressions: int = 5,
    progress: bool = False,
) -> dict:
    """
    Re-run captured queries against current search and compute drift.

    Metrics (all path-level — chunk_id schema changes do not affect these):
        mean_jaccard_at_k  — average overlap of retrieved_paths sets, truncated to k
        top1_stability     — fraction of queries whose #1 result path stayed identical
        mean_latency_delta_ms — mean (current − captured), None if no captured latency

    Side effects are suppressed during replay:
        - record_access=False  → no ACT-R weight pollution
        - MEMOSYNE_CAPTURE_QUERIES override forced off → no log feedback loop

    Returns dict with summary + top-N regressions sorted by jaccard ascending.
    """
    events = _load_baseline(baseline_path)
    if not events:
        return {
            "baseline_path":          str(baseline_path),
            "n_queries":              0,
            "mean_jaccard_at_k":      None,
            "top1_stability":         None,
            "mean_latency_delta_ms":  None,
            "regressions":            [],
            "note":                   "no replayable events in baseline",
        }

    # Lazy import to keep query_log standalone-testable when vectorize is absent
    try:
        from vectorize import search as _search
    except ImportError as exc:
        raise RuntimeError(
            f"replay requires vectorize.search: {exc}"
        ) from exc

    # Prevent feedback loop: replay queries must not pollute the log
    prior_override = _capture_override
    set_capture_override(False)
    # And cannot rely on env var being unset — temporarily clear it
    prior_env = os.environ.pop("MEMOSYNE_CAPTURE_QUERIES", None)

    rows: list[dict] = []
    latency_deltas: list[int] = []

    try:
        for i, ev in enumerate(events):
            query = ev["query"]
            captured_paths = list(ev["retrieved_paths"])[:top_k]
            t0 = datetime.now()
            try:
                results = _search(query, top_k=top_k, record_access=False)
                current_paths = [r["path"] for r in results][:top_k]
                error = None
            except Exception as exc:  # noqa: BLE001
                current_paths = []
                error = f"{type(exc).__name__}: {exc}"
            latency_ms = int((datetime.now() - t0).total_seconds() * 1000)

            old_set = set(captured_paths)
            new_set = set(current_paths)
            jacc = _jaccard(old_set, new_set)
            top1_same = (
                len(captured_paths) > 0
                and len(current_paths) > 0
                and captured_paths[0] == current_paths[0]
            )

            captured_latency = ev.get("latency_ms")
            if isinstance(captured_latency, (int, float)):
                latency_deltas.append(latency_ms - int(captured_latency))

            rows.append({
                "query":            query,
                "ts":               ev.get("ts"),
                "captured_paths":   captured_paths,
                "current_paths":    current_paths,
                "jaccard":          round(jacc, 4),
                "top1_same":        bool(top1_same),
                "current_latency_ms": latency_ms,
                "captured_latency_ms": captured_latency,
                "error":            error,
            })

            if progress and (i + 1) % 10 == 0:
                print(f"  [replay] {i + 1}/{len(events)}", file=sys.stderr)
    finally:
        # Restore prior capture state
        set_capture_override(prior_override)
        if prior_env is not None:
            os.environ["MEMOSYNE_CAPTURE_QUERIES"] = prior_env

    valid = [r for r in rows if r["error"] is None]
    mean_jacc = sum(r["jaccard"] for r in valid) / len(valid) if valid else None
    top1_pct = (
        sum(1 for r in valid if r["top1_same"]) / len(valid) if valid else None
    )
    mean_latency_delta = (
        sum(latency_deltas) / len(latency_deltas) if latency_deltas else None
    )

    regressions = sorted(
        [r for r in valid if r["jaccard"] < 1.0],
        key=lambda r: (r["jaccard"], r["ts"] or ""),
    )[:top_n_regressions]

    return {
        "baseline_path":          str(baseline_path),
        "top_k":                  top_k,
        "n_queries":              len(rows),
        "n_errors":               len(rows) - len(valid),
        "mean_jaccard_at_k":      round(mean_jacc, 4) if mean_jacc is not None else None,
        "top1_stability":         round(top1_pct, 4) if top1_pct is not None else None,
        "mean_latency_delta_ms":  int(mean_latency_delta) if mean_latency_delta is not None else None,
        "regressions":            regressions,
    }


def _print_replay_summary(report: dict) -> None:
    print("🜍 The Augury Replay — drift report")
    print(f"   baseline:               {report['baseline_path']}")
    print(f"   n_queries:              {report['n_queries']}  (errors: {report.get('n_errors', 0)})")
    print(f"   top_k:                  {report.get('top_k')}")
    print(f"   mean_jaccard@k:         {report['mean_jaccard_at_k']}")
    print(f"   top1_stability:         {report['top1_stability']}")
    print(f"   mean_latency_delta_ms:  {report['mean_latency_delta_ms']}")
    regs = report.get("regressions") or []
    if regs:
        print(f"\n   Top {len(regs)} regressions (lowest Jaccard first):")
        for r in regs:
            q = r["query"]
            q_short = (q[:60] + "…") if len(q) > 60 else q
            print(f"     · jaccard={r['jaccard']:.2f}  top1_same={r['top1_same']}  "
                  f"q={q_short!r}")
    else:
        note = report.get("note")
        if note:
            print(f"   note: {note}")
        else:
            print("\n   No regressions — all replayed queries match baseline exactly.")


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
                    help="replay a previously exported baseline NDJSON; "
                         "compares retrieved paths against current search")
    ap.add_argument("--top-k", type=int, default=10,
                    help="top-k for replay (default 10)")
    ap.add_argument("--top-n-regressions", type=int, default=5,
                    help="how many worst-jaccard queries to print (default 5)")
    ap.add_argument("--json", action="store_true",
                    help="emit replay report as JSON instead of human summary")
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
            report = replay(
                Path(args.replay),
                top_k=args.top_k,
                top_n_regressions=args.top_n_regressions,
                progress=True,
            )
        except (FileNotFoundError, RuntimeError) as e:
            print(f"[replay] {e}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_replay_summary(report)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
