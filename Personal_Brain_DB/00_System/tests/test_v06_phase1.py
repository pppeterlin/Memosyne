#!/usr/bin/env python3
"""
Tests for v0.6 Phase 1: turn-aware Gemini ingest update.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS.parent))

import ingest


GEMINI_V1 = """---
uuid: "abc12345"
title: "Test conversation"
date_created: 2026-05-01
date_updated: 2026-05-01
type: "chat"
source: "gemini"
tags: ["test"]
related_entities: []
summary: "test"
---

# Test conversation

> **對話連結：** https://gemini.google.com/app/c_test

---

## 使用者
First user message.

## Gemini
First assistant reply.
"""

GEMINI_V2 = GEMINI_V1 + """
## 使用者
Second user message — appended later.

## Gemini
Second assistant reply with new content.
"""


class TestPhase1Helpers(unittest.TestCase):
    def test_extract_uuid(self):
        self.assertEqual(ingest._extract_fm_field(GEMINI_V1, "uuid"), "abc12345")

    def test_extract_absent(self):
        self.assertEqual(ingest._extract_fm_field(GEMINI_V1, "nonexistent"), "")

    def test_bump_date_updated_replaces(self):
        out = ingest._bump_date_updated(GEMINI_V1, "2026-12-31")
        self.assertIn("date_updated: 2026-12-31", out)
        self.assertNotIn("date_updated: 2026-05-01", out)

    def test_bump_date_updated_inserts(self):
        content = '---\nuuid: "x"\n---\nbody\n'
        out = ingest._bump_date_updated(content, "2026-12-31")
        self.assertIn("date_updated: 2026-12-31", out)

    def test_clear_enriched_at(self):
        content = ('---\nuuid: "x"\nenriched_at: "2026-05-10T12:00:00"\n'
                   'importance: high\n---\nbody\n')
        out = ingest._clear_enriched_at(content)
        self.assertNotIn("enriched_at", out)
        self.assertIn("importance: high", out)  # other fields untouched


class TestTurnAwareUpdate(unittest.TestCase):
    """
    Exercise _try_turn_aware_gemini_update directly. Uses isolated
    chronicle.db (via turn_ledger.LEDGER_DB monkeypatch) and a temp
    vault dir.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dst = self.tmp / "conv_abc.md"
        self.dst.write_text(GEMINI_V1, encoding="utf-8")

        # Isolate the turn ledger
        import turn_ledger as tl
        self._original_ledger = tl.LEDGER_DB
        self.ledger_file = self.tmp / "ledger.sqlite"
        tl.LEDGER_DB = self.ledger_file
        self.tl = tl

        # Point ingest's BRAIN_DB at our temp so relative_to works
        self._original_brain = ingest.BRAIN_DB
        ingest.BRAIN_DB = self.tmp

    def tearDown(self):
        self.tl.LEDGER_DB = self._original_ledger
        ingest.BRAIN_DB = self._original_brain
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identical_content_skipped(self):
        result = ingest._try_turn_aware_gemini_update(
            GEMINI_V1, self.dst, GEMINI_V1, dry_run=False
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "skipped_same")

    def test_new_turns_trigger_update(self):
        result = ingest._try_turn_aware_gemini_update(
            GEMINI_V2, self.dst, GEMINI_V1, dry_run=False
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "updated")
        self.assertIn("+2 new turns", result.note)  # 2 new turns appended

    def test_update_preserves_uuid(self):
        ingest._try_turn_aware_gemini_update(GEMINI_V2, self.dst, GEMINI_V1, dry_run=False)
        new_content = self.dst.read_text(encoding="utf-8")
        self.assertIn('uuid: "abc12345"', new_content)

    def test_update_clears_enriched_at(self):
        # Pretend the file was already enriched
        original_with_enriched = GEMINI_V1.replace(
            'summary: "test"',
            'summary: "test"\nenriched_at: "2026-05-10T12:00:00"',
        )
        self.dst.write_text(original_with_enriched, encoding="utf-8")
        ingest._try_turn_aware_gemini_update(
            GEMINI_V2, self.dst, original_with_enriched, dry_run=False
        )
        new_content = self.dst.read_text(encoding="utf-8")
        self.assertNotIn("enriched_at", new_content)

    def test_update_bumps_date_updated(self):
        ingest._try_turn_aware_gemini_update(GEMINI_V2, self.dst, GEMINI_V1, dry_run=False)
        new_content = self.dst.read_text(encoding="utf-8")
        # Should NOT still be the original 2026-05-01
        self.assertNotIn("date_updated: 2026-05-01", new_content)

    def test_ledger_records_all_turns(self):
        ingest._try_turn_aware_gemini_update(GEMINI_V2, self.dst, GEMINI_V1, dry_run=False)
        all_hashes = self.tl.known_turn_hashes(memory_uuid="abc12345")
        # 4 turns total in V2 (2 original + 2 new)
        self.assertEqual(len(all_hashes), 4)

    def test_second_update_with_no_new_turns_is_dup(self):
        # First update: should succeed
        r1 = ingest._try_turn_aware_gemini_update(GEMINI_V2, self.dst, GEMINI_V1, dry_run=False)
        self.assertEqual(r1.action, "updated")
        # Second update with same content: should detect as duplicate
        r2 = ingest._try_turn_aware_gemini_update(
            GEMINI_V2, self.dst, self.dst.read_text(), dry_run=False
        )
        self.assertEqual(r2.action, "skipped_same")

    def test_unparseable_returns_none(self):
        # Content without ## 使用者 / ## Gemini headers
        bad_spring = '---\nuuid: "abc12345"\n---\n\nno turn headers here\n'
        result = ingest._try_turn_aware_gemini_update(
            bad_spring, self.dst, GEMINI_V1, dry_run=False
        )
        self.assertIsNone(result)

    def test_missing_uuid_in_dst_returns_none(self):
        no_uuid = GEMINI_V1.replace('uuid: "abc12345"', 'title: "no uuid"')
        self.dst.write_text(no_uuid, encoding="utf-8")
        result = ingest._try_turn_aware_gemini_update(
            GEMINI_V2, self.dst, no_uuid, dry_run=False
        )
        self.assertIsNone(result)

    def test_dirty_marker_written_on_update(self):
        # Override dirty marker location to temp
        import ingest as ig
        original_marker = ig._dirty_marker_path
        marker_file = self.tmp / "dirty.txt"
        ig._dirty_marker_path = lambda: marker_file
        try:
            ingest._try_turn_aware_gemini_update(GEMINI_V2, self.dst, GEMINI_V1, dry_run=False)
            self.assertTrue(marker_file.exists())
            self.assertIn("conv_abc.md", marker_file.read_text())
        finally:
            ig._dirty_marker_path = original_marker


if __name__ == "__main__":
    unittest.main()
