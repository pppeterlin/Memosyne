"""The Digest: deterministic timeline + entity index built from frontmatter."""

import tempfile
import unittest
from pathlib import Path

from memosyne import digest

ENRICHED = """---
title: "Lake week"
date_created: 2025-06-04
period: "2025 lake residency"
entities:
  locations: ["Lake B", "Mount C"]
  people: ["Alex"]
  events: ["residency trip"]
---

Arrived at Lake B with Alex.
"""


class TestDigest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        self.write("30_Journal/2025/250604.md", ENRICHED)
        self.write("30_Journal/2025/250712.md",
                   "---\ntype: journal\n---\n\n# Second visit\n\nBack at [[location:Lake B]].\n")
        self.write("30_Journal/2024/note.md", "No frontmatter, no date.\n")
        self.write("20_AI_Chats/Gemini/chat.md", "---\ndate_created: 2025-06-05\n---\n\nQ&A\n")
        self.write("30_Journal/2025/250801.md", "---\ndormant: true\n---\n\nForgotten.\n")
        self.write("_digest/old.md", "stale")

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_collect_skips_chats_dormant_and_its_own_output(self):
        got = [e.path for e in digest.collect(self.vault)]
        self.assertEqual(got, ["30_Journal/2025/250604.md", "30_Journal/2025/250712.md",
                               "30_Journal/2024/note.md"])
        with_chats = [e.path for e in digest.collect(self.vault, include_chats=True)]
        self.assertIn("20_AI_Chats/Gemini/chat.md", with_chats)

    def test_dates_come_from_frontmatter_then_filename(self):
        by_path = {e.path: e for e in digest.collect(self.vault)}
        self.assertEqual(by_path["30_Journal/2025/250604.md"].date, "2025-06-04")
        self.assertEqual(by_path["30_Journal/2025/250712.md"].date, "2025-07-12")
        self.assertEqual(by_path["30_Journal/2024/note.md"].date, "")
        self.assertEqual(by_path["30_Journal/2025/250712.md"].title, "Second visit")

    def test_timeline_groups_by_year_and_month(self):
        text = digest.render_timeline(digest.collect(self.vault))
        self.assertIn("> 3 memories · 2025-06 → 2025-07", text)
        self.assertLess(text.index("### 2025-06"), text.index("### 2025-07"))
        self.assertLess(text.index("### 2025-07"), text.index("## Undated"))
        self.assertIn("- **04** `30_Journal/2025/250604.md` — Lake week · "
                      "period: 2025 lake residency · places: Lake B, Mount C · "
                      "people: Alex · events: residency trip", text)

    def test_entity_index_counts_memories_and_spans_dates(self):
        text = digest.render_entities(digest.collect(self.vault))
        self.assertIn("> 2 places · 1 people · 1 events · 1 periods", text)
        self.assertIn("- **Lake B** (2) · 2025-06 → 2025-07: "
                      "`30_Journal/2025/250604.md`, `30_Journal/2025/250712.md`", text)
        self.assertIn("- **Mount C** (1) · 2025-06: `30_Journal/2025/250604.md`", text)

    def test_long_entity_lists_are_truncated(self):
        for day in range(10, 20):
            self.write(f"30_Journal/2025/2509{day}.md",
                       "---\nentities:\n  people: [\"Alex\"]\n---\n\nSaw Alex.\n")
        text = digest.render_entities(digest.collect(self.vault))
        line = next(ln for ln in text.splitlines() if ln.startswith("- **Alex**"))
        self.assertIn("(11)", line)
        self.assertEqual(line.count("`") // 2, digest.MAX_PATHS_PER_ENTITY)
        self.assertTrue(line.endswith("+5 more"))

    def test_build_is_idempotent_and_never_indexed(self):
        first = digest.build(self.vault)
        self.assertEqual(set(first), {"timeline", "entities"})
        stamps = {k: p.stat().st_mtime_ns for k, p in first.items()}
        again = digest.build(self.vault)
        self.assertEqual(stamps, {k: p.stat().st_mtime_ns for k, p in again.items()})
        from memosyne.paths import iter_memory_files
        self.assertFalse([p for p in iter_memory_files(self.vault) if "_digest" in p.parts])

    def test_read_builds_on_demand_and_filters_by_year(self):
        text = digest.read("timeline", self.vault)
        self.assertTrue(text.startswith("# Timeline"))
        only = digest.read("timeline", self.vault, year="2025")
        self.assertTrue(only.startswith("# Timeline — 2025"))
        self.assertNotIn("Undated", only)
        self.assertEqual(digest.read("timeline", self.vault, year="1999"),
                         "No dated memories in 1999.")
        with self.assertRaises(ValueError):
            digest.read("nope", self.vault)

    def test_empty_vault(self):
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        self.assertIn("> 0 memories · no dated memories", digest.render_timeline(digest.collect(empty)))
        self.assertIn("_None recorded yet", digest.render_entities([]))


if __name__ == "__main__":
    unittest.main()
