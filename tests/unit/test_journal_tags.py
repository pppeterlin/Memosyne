"""Journal auto-tags: generic defaults in code, personal vocabulary in the vault."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memosyne import ingest


class TestJournalTags(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name)
        self._patch = mock.patch.object(ingest, "BRAIN_DB", self.vault)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_defaults_apply_without_a_vault_file(self):
        self.assertEqual(ingest._journal_tags("今天去機場，順便反思了一下"), ["旅行", "自省"])

    def test_vault_file_overrides_extends_and_disables(self):
        (self.vault / "tags.yaml").write_text(
            '寵物: ["Saturn"]\n接案: ["提案"]\n自省: []\n', encoding="utf-8")
        self.assertEqual(ingest._journal_tags("Saturn 掉毛，提案改完，順便反思"), ["寵物", "接案"])
        self.assertEqual(ingest._journal_tags("鄰居的貓來了"), [])       # default list replaced

    def test_broken_vault_file_falls_back_to_defaults(self):
        (self.vault / "tags.yaml").write_text("寵物: [unclosed\n", encoding="utf-8")
        with mock.patch("builtins.print"):
            self.assertEqual(ingest._journal_tags("鄰居的貓來了"), ["寵物"])

    def test_no_personal_names_ship_in_the_defaults(self):
        words = [w for ws in ingest.DEFAULT_TAG_KEYWORDS.values() for w in ws]
        self.assertTrue(all(len(w) <= 3 for w in words), words)


if __name__ == "__main__":
    unittest.main()
