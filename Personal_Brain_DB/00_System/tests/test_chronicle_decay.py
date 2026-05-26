"""Unit tests for per-prefix decay (mneme_weight.py v0.5).

驗證 decay_for_path 的最長前綴匹配邏輯與 env override 解析。
不需要 chronicle.db 存在 — 純函式測試。

執行：
    python3 -m unittest tests.test_chronicle_decay -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mneme_weight as mw  # noqa: E402


class TestDecayForPath(unittest.TestCase):

    def setUp(self):
        # 清掉 env override，每個測試從預設開始
        os.environ.pop("MEMOSYNE_CHRONICLE_DECAY", None)
        mw.reset_decay_map()

    def tearDown(self):
        os.environ.pop("MEMOSYNE_CHRONICLE_DECAY", None)
        mw.reset_decay_map()

    def test_default_journal(self):
        self.assertEqual(mw.decay_for_path("30_Journal/2026/260112.md"), 0.50)

    def test_default_knowledge_is_more_evergreen_than_journal(self):
        d_journal = mw.decay_for_path("30_Journal/2025/250604.md")
        d_knowledge = mw.decay_for_path("50_Knowledge/python_notes.md")
        self.assertLess(d_knowledge, d_journal,
                        "Knowledge should decay slower than Journal")

    def test_default_profile_most_evergreen(self):
        d_profile = mw.decay_for_path("10_Profile/bio.md")
        self.assertLessEqual(d_profile, 0.25)

    def test_default_chat_decays_fastest(self):
        d_chat = mw.decay_for_path("20_AI_Chats/2025/foo.md")
        d_journal = mw.decay_for_path("30_Journal/2025/foo.md")
        self.assertGreater(d_chat, d_journal,
                           "Chat should decay faster than Journal")

    def test_unknown_prefix_falls_back_to_global(self):
        self.assertEqual(mw.decay_for_path("99_Random/file.md"), mw.DECAY_D)

    def test_empty_path_falls_back(self):
        self.assertEqual(mw.decay_for_path(""), mw.DECAY_D)

    def test_longest_prefix_wins(self):
        """同時匹配多個 prefix 時取最長。"""
        os.environ["MEMOSYNE_CHRONICLE_DECAY"] = (
            '{"30_Journal/": 0.5, "30_Journal/2025/": 0.7}'
        )
        mw.reset_decay_map()
        self.assertEqual(mw.decay_for_path("30_Journal/2025/250604.md"), 0.7)
        self.assertEqual(mw.decay_for_path("30_Journal/2026/260101.md"), 0.5)

    def test_env_override(self):
        os.environ["MEMOSYNE_CHRONICLE_DECAY"] = '{"30_Journal/": 0.9}'
        mw.reset_decay_map()
        self.assertEqual(mw.decay_for_path("30_Journal/x.md"), 0.9)

    def test_env_override_malformed_falls_back(self):
        """壞掉的 JSON 不該 crash，應該降級到預設。"""
        os.environ["MEMOSYNE_CHRONICLE_DECAY"] = "{this is not json"
        mw.reset_decay_map()
        # 預設仍可用
        self.assertEqual(mw.decay_for_path("30_Journal/x.md"), 0.50)

    def test_env_override_wrong_type_falls_back(self):
        os.environ["MEMOSYNE_CHRONICLE_DECAY"] = "[1, 2, 3]"
        mw.reset_decay_map()
        self.assertEqual(mw.decay_for_path("30_Journal/x.md"), 0.50)


if __name__ == "__main__":
    unittest.main()
