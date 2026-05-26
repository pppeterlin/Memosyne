#!/usr/bin/env python3
"""
Memosyne — Turn parsing for accumulating sources (v0.6).

An "accumulating source" is a memory whose canonical file grows over
time: Gemini conversation continuations, journal append, exported chat
logs. The same export gets re-imported with previous turns plus new
ones. Treating the file as the unit of identity loses the new turns
(silent skip) or duplicates the whole thing (filename collision).

The Turn abstraction lets us identify the unit of memory at the
right granularity:

    file = container, turn = identity

Each TurnParser knows how to split one source format into Turn objects.
Turn equality is by content hash, so re-imports of the same conversation
recognize their prior turns and only emit the new ones to enrich +
vectorize.

See docs/v0.6_accumulating_sources.md for design rationale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Protocol

from content_hash import turn_hash


@dataclass(frozen=True)
class Turn:
    """One unit of memory within an accumulating-source file."""

    index: int          # position within the file (0-based)
    role: str           # "user" | "assistant" | "self" | ...
    text: str           # the turn's body text (without role header)
    raw: str            # original slice from the file, used for hashing

    @property
    def hash(self) -> str:
        """Stable content-based identity. Same text → same hash across re-exports."""
        return turn_hash(self.raw)


class TurnParser(Protocol):
    """A source-specific splitter. Each accumulating source has one."""

    source: str  # short identifier: "gemini" | "claude" | "journal_append"

    def can_parse(self, content: str) -> bool:
        """Cheap sniff: does this content look like our format?"""
        ...

    def split_turns(self, content: str) -> list[Turn]:
        """Return turns in file order. Empty list if format doesn't match."""
        ...


# ─── Gemini export ───────────────────────────────────────────
#
# Format (observed from real exports):
#
#   ---
#   <yaml frontmatter>
#   ---
#
#   # <title>
#   > **備份時間：** ...
#   > **對話連結：** https://gemini.google.com/app/c_<hash>
#
#   ---
#
#   ## 使用者
#   <user turn>
#
#   ## Gemini
#   ## Gemini 說了
#   <assistant turn>
#
#   ## 使用者
#   ...
#
# Stable delimiters: lines starting with `## 使用者` or `## Gemini`.
# Everything before the first delimiter is preamble (title + metadata).
# Everything between two delimiters is one turn.

class GeminiParser:
    source = "gemini"

    _TURN_HEADER = re.compile(r"^##\s+(使用者|Gemini)\s*(說了)?\s*$", re.MULTILINE)
    _SIGNATURE = re.compile(r"gemini\.google\.com|## 使用者")

    def can_parse(self, content: str) -> bool:
        return bool(self._SIGNATURE.search(content))

    def split_turns(self, content: str) -> list[Turn]:
        # Find every turn header position
        markers = [(m.start(), m.group(1), m.end()) for m in self._TURN_HEADER.finditer(content)]
        if not markers:
            return []

        # Skip consecutive "Gemini" + "Gemini 說了" — Gemini sometimes
        # emits both as adjacent headers introducing the same turn.
        deduped: list[tuple[int, str, int]] = []
        for start, who, end in markers:
            if deduped and deduped[-1][1] == "Gemini" and who == "Gemini":
                # Same speaker, immediately after — merge by moving end forward
                deduped[-1] = (deduped[-1][0], who, end)
            else:
                deduped.append((start, who, end))

        turns: list[Turn] = []
        for i, (start, who, header_end) in enumerate(deduped):
            next_start = deduped[i + 1][0] if i + 1 < len(deduped) else len(content)
            raw = content[start:next_start].rstrip()
            text = content[header_end:next_start].strip()
            if not text:
                continue  # empty turn — skip
            role = "user" if who == "使用者" else "assistant"
            turns.append(Turn(index=i, role=role, text=text, raw=raw))
        return turns


# ─── Claude export ───────────────────────────────────────────
#
# Format (observed):
#
#   ## Human
#   <user turn>
#
#   ## Assistant
#   <assistant turn>
#
# Same shape as Gemini but English headers.

class ClaudeParser:
    source = "claude"

    _TURN_HEADER = re.compile(r"^##\s+(Human|Assistant)\s*$", re.MULTILINE)
    _SIGNATURE = re.compile(r"claude\.ai|## Human|## Assistant")

    def can_parse(self, content: str) -> bool:
        return bool(self._SIGNATURE.search(content))

    def split_turns(self, content: str) -> list[Turn]:
        markers = [(m.start(), m.group(1), m.end()) for m in self._TURN_HEADER.finditer(content)]
        if not markers:
            return []

        turns: list[Turn] = []
        for i, (start, who, header_end) in enumerate(markers):
            next_start = markers[i + 1][0] if i + 1 < len(markers) else len(content)
            raw = content[start:next_start].rstrip()
            text = content[header_end:next_start].strip()
            if not text:
                continue
            role = "user" if who == "Human" else "assistant"
            turns.append(Turn(index=i, role=role, text=text, raw=raw))
        return turns


# ─── Journal append ──────────────────────────────────────────
#
# A journal file with day sub-sections. The user adds new days at the
# bottom over time; treating each day as a turn means re-importing the
# same journal only enriches/embeds the new days.
#
# Format expected:
#
#   ## 2026-05-26
#   <body>
#
#   ## 2026-05-27
#   <body>
#
# Anything before the first day heading is preamble.

class JournalAppendParser:
    source = "journal_append"

    _DAY_HEADER = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)

    def can_parse(self, content: str) -> bool:
        # Need at least two day headings before we treat as accumulating —
        # a single day journal is fine to keep file-level.
        return len(self._DAY_HEADER.findall(content)) >= 2

    def split_turns(self, content: str) -> list[Turn]:
        markers = [(m.start(), m.group(1), m.end()) for m in self._DAY_HEADER.finditer(content)]
        if not markers:
            return []

        turns: list[Turn] = []
        for i, (start, date, header_end) in enumerate(markers):
            next_start = markers[i + 1][0] if i + 1 < len(markers) else len(content)
            raw = content[start:next_start].rstrip()
            text = content[header_end:next_start].strip()
            if not text:
                continue
            turns.append(Turn(index=i, role=f"self:{date}", text=text, raw=raw))
        return turns


# ─── Registry ────────────────────────────────────────────────

_PARSERS: list[TurnParser] = [
    GeminiParser(),
    ClaudeParser(),
    JournalAppendParser(),
]


def get_parser(source: str) -> TurnParser | None:
    """Look up a parser by its source identifier."""
    for p in _PARSERS:
        if p.source == source:
            return p
    return None


def detect_parser(content: str, hint: str = "") -> TurnParser | None:
    """
    Pick the right parser for this content.

    Priority:
      1. hint (e.g. frontmatter `source:` field) wins if it matches a parser
      2. Otherwise the first parser whose can_parse() returns True
      3. None if nothing matches — caller should treat as file-level memory

    Returns None for content that isn't an accumulating source — those
    should keep going through the existing file-level ingest path.
    """
    if hint:
        p = get_parser(hint)
        if p and p.can_parse(content):
            return p
    for p in _PARSERS:
        if p.can_parse(content):
            return p
    return None


def diff_against_known(turns: list[Turn], known_hashes: Iterable[str]) -> list[Turn]:
    """Return only turns whose hash is not in known_hashes (preserving order)."""
    known = set(known_hashes)
    return [t for t in turns if t.hash not in known]
