#!/usr/bin/env python3
"""
Memosyne — Token auth for The Open Threshold (MCP HTTP)

Local-only bearer-token authorization for the HTTP MCP transport.
v0.5 deliberately keeps this simple — no OAuth, no refresh, no admin
console. Tokens are random 32-byte secrets, stored only as SHA-256
hashes alongside a human label and a scope.

Storage: ~/.memosyne/tokens.sqlite (override with MEMOSYNE_AUTH_DB env)

Schema:
    tokens(
        label       TEXT  PRIMARY KEY,
        hash        TEXT  NOT NULL UNIQUE,
        scope       TEXT  NOT NULL,   -- read | write | admin
        created_at  TEXT  NOT NULL,
        last_used   TEXT  NULL,
        revoked_at  TEXT  NULL
    )

Scope ladder (each implies the ones above it):
    read  → list_journals, search_memory, get_profile, get_memory_health, …
    write → + ingest hooks, slumber, query_log capture toggle
    admin → + aletheia *_apply, correct revert, dangerous operations

Scope is enforced by mcp_server.py's auth middleware, not by this
module. This module only stores and checks.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal

Scope = Literal["read", "write", "admin"]
SCOPE_ORDER = {"read": 0, "write": 1, "admin": 2}


def _db_path() -> Path:
    raw = os.getenv("MEMOSYNE_AUTH_DB", "")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".memosyne" / "tokens.sqlite"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tokens (
            label       TEXT PRIMARY KEY,
            hash        TEXT NOT NULL UNIQUE,
            scope       TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            last_used   TEXT,
            revoked_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tokens_hash ON tokens(hash);
        """
    )
    conn.commit()
    return conn


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ─── Public CRUD ─────────────────────────────────────────────

def create(label: str, scope: Scope = "read") -> str:
    """
    Mint a new token. Returns the plaintext token (shown ONCE — caller
    must capture immediately; we only store the hash).
    """
    if scope not in SCOPE_ORDER:
        raise ValueError(f"invalid scope {scope!r}; must be read|write|admin")
    if not label or not label.replace("-", "").replace("_", "").isalnum():
        raise ValueError("label must be alphanumeric (dash/underscore allowed)")

    token = secrets.token_urlsafe(32)
    h = _hash(token)
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO tokens(label, hash, scope, created_at) VALUES (?, ?, ?, ?)",
            (label, h, scope, _now()),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        raise ValueError(f"label {label!r} already exists") from e
    finally:
        conn.close()
    return token


def list_tokens() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT label, scope, created_at, last_used, revoked_at FROM tokens "
            "ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "label":      r[0],
            "scope":      r[1],
            "created_at": r[2],
            "last_used":  r[3],
            "revoked_at": r[4],
            "active":     r[4] is None,
        }
        for r in rows
    ]


def revoke(label: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE tokens SET revoked_at = ? "
            "WHERE label = ? AND revoked_at IS NULL",
            (_now(), label),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def lookup(token: str) -> dict | None:
    """
    Return the row matching this token (by hash), or None if no match
    or revoked. Touches last_used as a side effect on successful lookup.
    """
    if not token:
        return None
    h = _hash(token)
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT label, scope, created_at, revoked_at FROM tokens WHERE hash = ?",
            (h,),
        ).fetchone()
        if row is None or row[3] is not None:
            return None
        conn.execute("UPDATE tokens SET last_used = ? WHERE hash = ?", (_now(), h))
        conn.commit()
        return {
            "label":      row[0],
            "scope":      row[1],
            "created_at": row[2],
        }
    finally:
        conn.close()


def scope_allows(token_scope: str, required: Scope) -> bool:
    """True iff token_scope is at least as privileged as required."""
    return SCOPE_ORDER.get(token_scope, -1) >= SCOPE_ORDER.get(required, 99)


# ─── CLI ─────────────────────────────────────────────────────

def _main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Memosyne auth — token CRUD")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_c = sub.add_parser("create", help="mint a new token")
    ap_c.add_argument("label", help="human label, e.g. cursor-laptop")
    ap_c.add_argument("--scope", choices=["read", "write", "admin"], default="read")

    sub.add_parser("list", help="show all tokens (active and revoked)")

    ap_r = sub.add_parser("revoke", help="revoke a token by label")
    ap_r.add_argument("label")

    args = ap.parse_args()

    if args.cmd == "create":
        try:
            tok = create(args.label, args.scope)
        except ValueError as e:
            print(f"[auth] {e}", file=__import__("sys").stderr)
            return 1
        print(f"label:  {args.label}")
        print(f"scope:  {args.scope}")
        print(f"token:  {tok}")
        print("\nStore this token now — it will not be shown again.")
        return 0

    if args.cmd == "list":
        rows = list_tokens()
        if not rows:
            print("(no tokens)")
            return 0
        for r in rows:
            state = "active" if r["active"] else "revoked"
            last = r["last_used"] or "—"
            print(f"  {r['label']:24s}  scope={r['scope']:5s}  state={state:7s}  last_used={last}")
        return 0

    if args.cmd == "revoke":
        ok = revoke(args.label)
        if ok:
            print(f"revoked: {args.label}")
            return 0
        print(f"no active token with label {args.label!r}", file=__import__("sys").stderr)
        return 1

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
