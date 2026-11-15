#!/usr/bin/env python3
"""
Memosyne — content_hash util

Stable SHA-256 hashing of memory bodies that:

  - Ignores leading / trailing whitespace
  - Normalizes line endings (CRLF → LF)
  - Collapses runs of blank lines (so re-formatters don't change the hash)
  - Strips frontmatter before hashing (the body is what carries truth;
    the frontmatter is metadata that can move independently)

Used by:
  - ingest.py — to decide whether a re-imported file is genuinely new
  - turn_ledger — to recognize the same turn across re-exports
  - tests + future health check — to verify frontmatter content_hash
    matches the actual body
"""

from __future__ import annotations

import hashlib
import re

_BLANK_LINES = re.compile(r"\n\s*\n+")


def normalize_body(text: str) -> str:
    """
    Canonicalize a body string for hashing.

    Returns the normalized text — callers can hash it or compare it.
    """
    if not text:
        return ""
    # CRLF → LF, strip BOM
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    # Trim trailing whitespace per line so re-saves don't churn the hash
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # Collapse runs of blank lines to a single blank line
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def strip_frontmatter(content: str) -> tuple[str, str]:
    """
    Split a markdown file into (frontmatter_block, body).

    frontmatter_block includes the surrounding `---` markers if present,
    else returns ("", content).
    """
    if not content.startswith("---"):
        return "", content
    # Find the closing --- after the first ---
    end = content.find("\n---", 3)
    if end == -1:
        return "", content
    fm_end = content.find("\n", end + 1)
    if fm_end == -1:
        return content, ""
    return content[: fm_end + 1], content[fm_end + 1 :]


def body_hash(content: str) -> str:
    """
    SHA-256 of the normalized body (frontmatter stripped).

    Returns a hex string like "sha256:abc123...".
    """
    _, body = strip_frontmatter(content)
    normalized = normalize_body(body)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def turn_hash(text: str) -> str:
    """
    SHA-256 of a single turn's text. Same normalization as body_hash but
    no frontmatter stripping (turns don't have frontmatter).

    Returns just the hex digest (no prefix) — turn_hashes go into a
    SQL primary key where the "sha256:" prefix would be noise.
    """
    normalized = normalize_body(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
