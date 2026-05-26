"""Unit tests for the Deterministic Loom (link_extractor.py).

執行：
    cd Personal_Brain_DB/00_System
    python3 -m pytest tests/test_link_extractor.py -v

或不裝 pytest 直接跑：
    python3 -m unittest tests.test_link_extractor

依賴：pyyaml（與 enrich.py 共用）。沒有 yaml 時 frontmatter 解析會回空 dict，
測試會略過 frontmatter 相關案例（標記 skip）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 讓 tests/ 能 import 上一層的 link_extractor
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import link_extractor as le  # noqa: E402

try:
    import yaml  # noqa: F401
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class TestFromContent(unittest.TestCase):

    def test_empty_input(self):
        r = le.extract_from_content("")
        self.assertEqual(r["entities"]["people"], [])
        self.assertEqual(r["entities"]["locations"], [])
        self.assertEqual(r["entities"]["events"], [])
        self.assertEqual(r["period"], "")
        self.assertEqual(r["_source"], "deterministic")

    def test_no_frontmatter_no_links(self):
        """Pure prose with names — extractor MUST NOT NLP-extract."""
        content = "Today I met Alice at Café Coucou in Paris. We talked about a project."
        r = le.extract_from_content(content)
        self.assertEqual(r["entities"]["people"], [])
        self.assertEqual(r["entities"]["locations"], [])

    def test_qualified_wikilinks(self):
        content = "Met [[person:Alice]] at [[place:Paris]] for [[event:Project Kickoff]]."
        r = le.extract_from_content(content)
        self.assertIn("Alice", r["entities"]["people"])
        self.assertIn("Paris", r["entities"]["locations"])
        self.assertIn("Project Kickoff", r["entities"]["events"])

    def test_qualified_wikilinks_with_display(self):
        content = "Met [[person:Alice|親愛的 Alice]] today."
        r = le.extract_from_content(content)
        # display name 被用作 entity name（gbrain 也是這個慣例）
        self.assertIn("親愛的 Alice", r["entities"]["people"])

    def test_at_mentions(self):
        content = "@Alice and @bob-the-builder met today."
        r = le.extract_from_content(content)
        self.assertIn("Alice", r["entities"]["people"])
        self.assertIn("bob-the-builder", r["entities"]["people"])

    def test_at_mention_rejects_single_char(self):
        """單字元 mention（如 @a）應被忽略，避免誤抽。"""
        content = "Look at @a or @b for context."
        r = le.extract_from_content(content)
        self.assertNotIn("a", r["entities"]["people"])
        self.assertNotIn("b", r["entities"]["people"])

    def test_at_mention_chinese(self):
        content = "今天和 @小明 一起吃飯。"
        r = le.extract_from_content(content)
        self.assertIn("小明", r["entities"]["people"])

    def test_hash_mentions_inline(self):
        content = "做完 (#deadline-march) 後就放假。"
        r = le.extract_from_content(content)
        self.assertIn("deadline-march", r["entities"]["events"])

    def test_hash_mentions_does_not_eat_heading(self):
        """Markdown heading（# at line start）不應被當成 event。"""
        content = "# Title\n\n## Subtitle\n\nbody"
        r = le.extract_from_content(content)
        self.assertEqual(r["entities"]["events"], [])

    def test_code_block_does_not_emit_entities(self):
        content = """
Outside text mentions @real-person.

```python
# inside fenced code: @fake-person and [[person:Ghost]] should NOT count
foo = "[[place:Atlantis]]"
```

Back to outside. Inline `@inline-fake` also ignored.
"""
        r = le.extract_from_content(content)
        self.assertIn("real-person", r["entities"]["people"])
        self.assertNotIn("fake-person", r["entities"]["people"])
        self.assertNotIn("Ghost", r["entities"]["people"])
        self.assertNotIn("Atlantis", r["entities"]["locations"])
        self.assertNotIn("inline-fake", r["entities"]["people"])

    def test_markdown_link_to_entity_dir_alone_does_not_classify(self):
        """純 markdown link 沒有 frontmatter 對應時不歸類（避免誤分類）。"""
        content = "See [Alice notes](10_Profile/people/alice)."
        r = le.extract_from_content(content)
        # 沒有 frontmatter 宣告 Alice 是 people，所以不歸類
        self.assertNotIn("Alice notes", r["entities"]["people"])

    @unittest.skipUnless(HAS_YAML, "pyyaml not installed")
    def test_frontmatter_entities_canonical(self):
        content = """---
period: "2025 Tokyo 求職期"
themes: ["職涯"]
entities:
  people: ["Alice", "Bob"]
  locations: ["Tokyo"]
  events: []
personal_facts:
  - "Alice 住在 Tokyo"
---

Just the body."""
        r = le.extract_from_content(content)
        self.assertEqual(r["period"], "2025 Tokyo 求職期")
        self.assertEqual(r["themes"], ["職涯"])
        self.assertIn("Alice", r["entities"]["people"])
        self.assertIn("Bob", r["entities"]["people"])
        self.assertIn("Tokyo", r["entities"]["locations"])
        self.assertEqual(r["personal_facts"], ["Alice 住在 Tokyo"])

    @unittest.skipUnless(HAS_YAML, "pyyaml not installed")
    def test_frontmatter_plus_body_recall_pass(self):
        """body 中出現 frontmatter 已宣告的 entity 字串 → 仍歸入 bucket（recall）."""
        content = """---
entities:
  people: ["Aiko"]
  locations: ["Tokyo"]
  events: []
---

Met Aiko in Tokyo yesterday. (Aiko was tired.)
"""
        r = le.extract_from_content(content)
        # frontmatter 已包含 → 仍歸類
        self.assertIn("Aiko", r["entities"]["people"])
        self.assertIn("Tokyo", r["entities"]["locations"])

    @unittest.skipUnless(HAS_YAML, "pyyaml not installed")
    def test_unqualified_wikilink_resolves_via_frontmatter(self):
        """[[name]] 無類型時，若 frontmatter 已宣告該名，歸到對應 bucket。"""
        content = """---
entities:
  people: ["Alice"]
  locations: []
  events: []
---

Met [[Alice]] at the cafe.
"""
        r = le.extract_from_content(content)
        self.assertIn("Alice", r["entities"]["people"])

    @unittest.skipUnless(HAS_YAML, "pyyaml not installed")
    def test_malformed_frontmatter_does_not_crash(self):
        content = """---
this: is not [valid yaml
period without colon
---

body"""
        r = le.extract_from_content(content)
        # 應安全降級到 empty
        self.assertEqual(r["entities"]["people"], [])
        self.assertEqual(r["period"], "")

    @unittest.skipUnless(HAS_YAML, "pyyaml not installed")
    def test_period_from_body_when_frontmatter_empty(self):
        content = """---
entities:
  people: []
  locations: []
  events: []
---

This was during [[period:2025 Tokyo 求職期]].
"""
        r = le.extract_from_content(content)
        self.assertEqual(r["period"], "2025 Tokyo 求職期")


class TestMerge(unittest.TestCase):

    def test_merge_unions_entities(self):
        a = {
            "entities": {"people": ["Alice"], "locations": ["Tokyo"], "events": []},
            "period": "P1", "personal_facts": ["fact1"], "themes": ["t1"],
        }
        b = {
            "entities": {"people": ["Bob", "Alice"], "locations": [], "events": ["E1"]},
            "period": "", "personal_facts": [], "themes": [],
        }
        m = le.merge_extractions(a, b)
        self.assertEqual(sorted(m["entities"]["people"]), ["Alice", "Bob"])
        self.assertEqual(m["entities"]["locations"], ["Tokyo"])
        self.assertEqual(m["entities"]["events"], ["E1"])
        # b 的 scalar 都空 → 保留 a 的
        self.assertEqual(m["period"], "P1")
        self.assertEqual(m["personal_facts"], ["fact1"])
        self.assertEqual(m["themes"], ["t1"])

    def test_merge_b_overrides_when_nonempty(self):
        a = {
            "entities": {"people": [], "locations": [], "events": []},
            "period": "P1", "personal_facts": ["a-fact"], "themes": ["t1"],
        }
        b = {
            "entities": {"people": [], "locations": [], "events": []},
            "period": "P2", "personal_facts": ["b-fact"], "themes": ["t2"],
        }
        m = le.merge_extractions(a, b)
        self.assertEqual(m["period"], "P2")
        self.assertEqual(m["personal_facts"], ["b-fact"])
        self.assertEqual(m["themes"], ["t2"])

    def test_merge_handles_missing_keys(self):
        """Merge 不該因為缺 key 而 crash。"""
        a = {"entities": {"people": ["X"]}}
        b = {}
        m = le.merge_extractions(a, b)
        self.assertIn("X", m["entities"]["people"])


if __name__ == "__main__":
    unittest.main()
