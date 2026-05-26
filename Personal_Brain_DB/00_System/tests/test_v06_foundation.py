#!/usr/bin/env python3
"""
Tests for v0.6 foundation: content_hash, turns, turn_ledger.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS.parent))

# Each test class isolates the ledger DB to a tempfile to avoid
# polluting the real chronicle.db.
import content_hash as ch
import turns as tu


class TestContentHash(unittest.TestCase):
    def test_normalize_collapses_blank_lines(self):
        a = "line one\n\n\n\nline two"
        b = "line one\n\nline two"
        self.assertEqual(ch.normalize_body(a), ch.normalize_body(b))

    def test_normalize_crlf_to_lf(self):
        self.assertEqual(
            ch.normalize_body("a\r\nb\r\n"),
            ch.normalize_body("a\nb\n"),
        )

    def test_normalize_strips_trailing_whitespace(self):
        self.assertEqual(
            ch.normalize_body("a    \nb"),
            ch.normalize_body("a\nb"),
        )

    def test_strip_frontmatter_present(self):
        content = '---\nfoo: bar\n---\n\nbody text\n'
        fm, body = ch.strip_frontmatter(content)
        self.assertIn("foo: bar", fm)
        self.assertEqual(body.strip(), "body text")

    def test_strip_frontmatter_absent(self):
        content = "no frontmatter here"
        fm, body = ch.strip_frontmatter(content)
        self.assertEqual(fm, "")
        self.assertEqual(body, content)

    def test_body_hash_stable_across_frontmatter_changes(self):
        a = '---\nfoo: 1\n---\n\nhello\n'
        b = '---\nfoo: 999\nbar: baz\n---\n\nhello\n'
        self.assertEqual(ch.body_hash(a), ch.body_hash(b))

    def test_body_hash_changes_with_body(self):
        a = '---\nfoo: 1\n---\n\nhello\n'
        b = '---\nfoo: 1\n---\n\nhello world\n'
        self.assertNotEqual(ch.body_hash(a), ch.body_hash(b))

    def test_body_hash_format(self):
        self.assertTrue(ch.body_hash("anything").startswith("sha256:"))


class TestGeminiParser(unittest.TestCase):
    SAMPLE = """---
uuid: "abc123"
source: "gemini"
---

# Title

> **對話連結：** https://gemini.google.com/app/c_xyz

---

## 使用者
First user message.

Some more user text.

## Gemini
## Gemini 說了

First assistant message.

## 使用者
Second user message.

## Gemini
Second assistant message.
"""

    def setUp(self):
        self.parser = tu.GeminiParser()

    def test_can_parse(self):
        self.assertTrue(self.parser.can_parse(self.SAMPLE))
        self.assertFalse(self.parser.can_parse("just some plain text"))

    def test_split_turns(self):
        turns = self.parser.split_turns(self.SAMPLE)
        self.assertEqual(len(turns), 4)
        self.assertEqual(turns[0].role, "user")
        self.assertIn("First user message", turns[0].text)
        self.assertEqual(turns[1].role, "assistant")
        self.assertIn("First assistant message", turns[1].text)
        self.assertEqual(turns[2].role, "user")
        self.assertEqual(turns[3].role, "assistant")

    def test_turn_hash_stable(self):
        turns_a = self.parser.split_turns(self.SAMPLE)
        # Add a new turn at the end — old turn hashes must not change
        extended = self.SAMPLE + "\n## 使用者\nThird user message.\n"
        turns_b = self.parser.split_turns(extended)
        self.assertEqual(turns_a[0].hash, turns_b[0].hash)
        self.assertEqual(turns_a[1].hash, turns_b[1].hash)
        self.assertEqual(turns_a[2].hash, turns_b[2].hash)
        self.assertEqual(turns_a[3].hash, turns_b[3].hash)
        self.assertEqual(len(turns_b), 5)

    def test_double_gemini_header_merged(self):
        """`## Gemini` then `## Gemini 說了` should collapse to one turn."""
        text = "## 使用者\nQ\n\n## Gemini\n## Gemini 說了\n\nA"
        turns = self.parser.split_turns(text)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[1].role, "assistant")
        self.assertIn("A", turns[1].text)

    def test_diff_against_known(self):
        turns = self.parser.split_turns(self.SAMPLE)
        known = {turns[0].hash, turns[1].hash}  # first 2 already ingested
        new = tu.diff_against_known(turns, known)
        self.assertEqual(len(new), 2)
        self.assertEqual(new[0].text, turns[2].text)


class TestClaudeParser(unittest.TestCase):
    SAMPLE = """## Human
ask one

## Assistant
answer one

## Human
ask two

## Assistant
answer two
"""

    def test_split(self):
        p = tu.ClaudeParser()
        self.assertTrue(p.can_parse(self.SAMPLE))
        turns = p.split_turns(self.SAMPLE)
        self.assertEqual(len(turns), 4)
        self.assertEqual([t.role for t in turns],
                         ["user", "assistant", "user", "assistant"])


class TestJournalAppendParser(unittest.TestCase):
    SAMPLE = """# Journal

Preamble.

## 2026-05-26
First day note.

## 2026-05-27
Second day note.
"""

    def test_needs_two_days(self):
        p = tu.JournalAppendParser()
        self.assertTrue(p.can_parse(self.SAMPLE))
        self.assertFalse(p.can_parse("## 2026-05-26\nonly one"))

    def test_split(self):
        p = tu.JournalAppendParser()
        turns = p.split_turns(self.SAMPLE)
        self.assertEqual(len(turns), 2)
        self.assertTrue(turns[0].role.startswith("self:"))
        self.assertIn("First day", turns[0].text)


class TestDetectParser(unittest.TestCase):
    def test_hint_wins(self):
        gemini_sample = "## 使用者\nq\n\n## Gemini\na"
        p = tu.detect_parser(gemini_sample, hint="gemini")
        self.assertIsInstance(p, tu.GeminiParser)

    def test_hint_ignored_if_wrong_format(self):
        # Wrong hint, content is Claude — should fall through to detection
        claude_sample = "## Human\nq\n\n## Assistant\na"
        p = tu.detect_parser(claude_sample, hint="gemini")
        self.assertIsInstance(p, tu.ClaudeParser)

    def test_none_for_file_level(self):
        p = tu.detect_parser("a plain note with no turn markers")
        self.assertIsNone(p)


class TestTurnLedger(unittest.TestCase):
    """Isolated ledger tests using MEMOSYNE_AUTH_DB-style override via env."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        # turn_ledger imports artifact_path at module load time, so we
        # patch its module-level LEDGER_DB after import.
        import turn_ledger as tl
        self._original = tl.LEDGER_DB
        tl.LEDGER_DB = Path(self.tmp.name)
        self.tl = tl

    def tearDown(self):
        self.tl.LEDGER_DB = self._original
        os.unlink(self.tmp.name)

    def test_empty_initially(self):
        self.assertEqual(self.tl.known_turn_hashes(), set())
        s = self.tl.stats()
        self.assertEqual(s["total_turns"], 0)

    def test_record_and_query(self):
        entries = [
            {"turn_hash": "h1", "memory_uuid": "u1", "memory_path": "p.md",
             "turn_index": 0, "source": "gemini"},
            {"turn_hash": "h2", "memory_uuid": "u1", "memory_path": "p.md",
             "turn_index": 1, "source": "gemini"},
        ]
        n = self.tl.record_turns(entries)
        self.assertEqual(n, 2)
        self.assertEqual(self.tl.known_turn_hashes(memory_uuid="u1"), {"h1", "h2"})

    def test_record_ignores_duplicates(self):
        entry = {"turn_hash": "h1", "memory_uuid": "u1", "memory_path": "p.md",
                 "turn_index": 0, "source": "gemini"}
        self.assertEqual(self.tl.record_turns([entry]), 1)
        self.assertEqual(self.tl.record_turns([entry]), 0)  # idempotent

    def test_mark_enriched(self):
        self.tl.record_turns([
            {"turn_hash": "h1", "memory_uuid": "u1", "memory_path": "p.md",
             "turn_index": 0, "source": "gemini"}
        ])
        self.assertEqual(self.tl.mark_enriched(["h1"]), 1)
        s = self.tl.stats()
        self.assertEqual(s["unprocessed"], 1)  # still not embedded
        self.tl.mark_embedded(["h1"])
        s = self.tl.stats()
        self.assertEqual(s["unprocessed"], 0)

    def test_scope_by_path(self):
        self.tl.record_turns([
            {"turn_hash": "ha", "memory_uuid": "u1", "memory_path": "a.md",
             "turn_index": 0, "source": "gemini"},
            {"turn_hash": "hb", "memory_uuid": "u2", "memory_path": "b.md",
             "turn_index": 0, "source": "gemini"},
        ])
        self.assertEqual(self.tl.known_turn_hashes(memory_path="a.md"), {"ha"})
        self.assertEqual(self.tl.known_turn_hashes(memory_path="b.md"), {"hb"})

    def test_reject_unknown_column(self):
        with self.assertRaises(ValueError):
            self.tl._set_flag(["h1"], "evil_col", 1)


if __name__ == "__main__":
    unittest.main()
