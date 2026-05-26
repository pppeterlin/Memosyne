#!/usr/bin/env python3
"""
Memosyne — Turn Ledger (v0.6)

SQLite ledger of every turn-level memory the vault has ever ingested.
Lives alongside the existing access_events table in chronicle.db.

Schema:

    CREATE TABLE turn_ledger (
        turn_hash    TEXT PRIMARY KEY,   -- sha256(normalized turn text)
        memory_uuid  TEXT NOT NULL,      -- which vault file owns this turn
        memory_path  TEXT NOT NULL,      -- redundant but cheap; saves a join
        turn_index   INTEGER NOT NULL,   -- position within that file
        source       TEXT NOT NULL,      -- gemini | claude | journal_append
        ingested_at  TEXT NOT NULL,
        enriched     INTEGER DEFAULT 0,  -- 0/1; was this turn fed to Oracle?
        embedded     INTEGER DEFAULT 0   -- 0/1; was this turn vector-indexed?
    );
    CREATE INDEX idx_turn_ledger_memory ON turn_ledger(memory_uuid);
    CREATE INDEX idx_turn_ledger_path   ON turn_ledger(memory_path);

Rules:
  - turn_hash is the unique identity; same content → same row, even if
    re-imported from a different file (rare, but well-defined)
  - memory_uuid stays stable across updates so Tapestry + Chronicle don't
    break edges when a file gains new turns
  - enriched/embedded are independent — a turn may be in the vault but
    not yet processed if a downstream stage failed
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Iterable

try:
    from artifacts import artifact_path, ensure_parent
except ImportError:  # standalone usage
    from pathlib import Path
    def artifact_path(name: str):
        return Path(__file__).parent / {"chronicle_db": "chronicle.db"}.get(name, name)
    def ensure_parent(p):
        p.parent.mkdir(parents=True, exist_ok=True)


LEDGER_DB = artifact_path("chronicle_db")


# ─── Connection / schema ─────────────────────────────────────

def _connect() -> sqlite3.Connection:
    ensure_parent(LEDGER_DB)
    conn = sqlite3.connect(str(LEDGER_DB))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS turn_ledger (
            turn_hash    TEXT PRIMARY KEY,
            memory_uuid  TEXT NOT NULL,
            memory_path  TEXT NOT NULL,
            turn_index   INTEGER NOT NULL,
            source       TEXT NOT NULL,
            ingested_at  TEXT NOT NULL,
            enriched     INTEGER DEFAULT 0,
            embedded     INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_turn_ledger_memory ON turn_ledger(memory_uuid);
        CREATE INDEX IF NOT EXISTS idx_turn_ledger_path   ON turn_ledger(memory_path);
        """
    )
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ─── Public API ──────────────────────────────────────────────

def known_turn_hashes(memory_uuid: str | None = None, memory_path: str | None = None) -> set[str]:
    """
    Return the set of turn_hashes already in the ledger.

    Scope (in order of precedence):
      - memory_uuid: only that vault file
      - memory_path: only that path (use when uuid not yet allocated)
      - None: every turn in the ledger (rare; usually you want a scope)
    """
    conn = _connect()
    try:
        if memory_uuid:
            rows = conn.execute(
                "SELECT turn_hash FROM turn_ledger WHERE memory_uuid = ?",
                (memory_uuid,),
            ).fetchall()
        elif memory_path:
            rows = conn.execute(
                "SELECT turn_hash FROM turn_ledger WHERE memory_path = ?",
                (memory_path,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT turn_hash FROM turn_ledger").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def record_turns(
    turn_entries: Iterable[dict],
    *,
    enriched: bool = False,
    embedded: bool = False,
) -> int:
    """
    Insert (or ignore) turn rows.

    Each dict in turn_entries should provide:
        turn_hash, memory_uuid, memory_path, turn_index, source

    Uses INSERT OR IGNORE so re-recording the same hash is safe — the
    first record wins and later attempts are no-ops, which matches our
    "same hash = same turn forever" invariant.

    Returns the number of new rows actually inserted.
    """
    conn = _connect()
    inserted = 0
    now = _now()
    try:
        for e in turn_entries:
            cur = conn.execute(
                "INSERT OR IGNORE INTO turn_ledger "
                "(turn_hash, memory_uuid, memory_path, turn_index, source, "
                " ingested_at, enriched, embedded) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    e["turn_hash"],
                    e["memory_uuid"],
                    e["memory_path"],
                    int(e["turn_index"]),
                    e["source"],
                    now,
                    1 if enriched else 0,
                    1 if embedded else 0,
                ),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


def mark_enriched(turn_hashes: Iterable[str]) -> int:
    return _set_flag(turn_hashes, "enriched", 1)


def mark_embedded(turn_hashes: Iterable[str]) -> int:
    return _set_flag(turn_hashes, "embedded", 1)


def _set_flag(turn_hashes: Iterable[str], col: str, val: int) -> int:
    if col not in {"enriched", "embedded"}:
        raise ValueError(f"refusing to update unknown column {col!r}")
    hashes = list(turn_hashes)
    if not hashes:
        return 0
    conn = _connect()
    try:
        placeholders = ",".join("?" for _ in hashes)
        cur = conn.execute(
            f"UPDATE turn_ledger SET {col} = ? WHERE turn_hash IN ({placeholders})",
            [val, *hashes],
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def stats() -> dict:
    """Aggregate stats over the ledger."""
    conn = _connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM turn_ledger").fetchone()[0]
        by_source = dict(
            conn.execute(
                "SELECT source, COUNT(*) FROM turn_ledger GROUP BY source"
            ).fetchall()
        )
        unprocessed = conn.execute(
            "SELECT COUNT(*) FROM turn_ledger WHERE enriched = 0 OR embedded = 0"
        ).fetchone()[0]
        n_memories = conn.execute(
            "SELECT COUNT(DISTINCT memory_uuid) FROM turn_ledger"
        ).fetchone()[0]
        return {
            "total_turns":        total,
            "distinct_memories":  n_memories,
            "by_source":          by_source,
            "unprocessed":        unprocessed,
            "ledger_db":          str(LEDGER_DB),
        }
    finally:
        conn.close()


# ─── CLI ─────────────────────────────────────────────────────

def _main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Memosyne turn ledger inspector")
    ap.add_argument("--stats", action="store_true", help="show aggregate counts")
    ap.add_argument("--for-uuid", default="",
                    help="list turn hashes for a given memory_uuid")
    ap.add_argument("--for-path", default="",
                    help="list turn hashes for a given memory_path")
    args = ap.parse_args()

    if args.stats:
        print(json.dumps(stats(), ensure_ascii=False, indent=2))
        return 0
    if args.for_uuid:
        for h in sorted(known_turn_hashes(memory_uuid=args.for_uuid)):
            print(h)
        return 0
    if args.for_path:
        for h in sorted(known_turn_hashes(memory_path=args.for_path)):
            print(h)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
