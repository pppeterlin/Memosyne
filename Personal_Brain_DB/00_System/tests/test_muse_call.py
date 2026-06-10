#!/usr/bin/env python3
"""
The Call of the Muses — unit tests
===================================

純 stdlib：用 tmp vault 驗證缺口偵測、冷卻、選題決定性與回答落地。
不需要 kuzu / chromadb（thin_person 在無 Tapestry 環境應靜默降級）。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import muse_call  # noqa: E402


class MuseCallBase(unittest.TestCase):
    """每個測試一個獨立 tmp vault + spring + ledger。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="muse_call_test_"))
        self.vault = self.tmp / "vault"
        self.spring = self.tmp / "spring"
        for domain in ("10_Profile", "30_Journal", "40_Projects", "50_Knowledge"):
            (self.vault / domain).mkdir(parents=True)
        self._old_env = {}
        for key, val in {
            "MEMOSYNE_VAULT_DIR": str(self.vault),
            "MEMOSYNE_ARTIFACT_DIR": str(self.tmp / "artifacts"),
            "MEMOSYNE_SPRING_DIR": str(self.spring),
        }.items():
            self._old_env[key] = os.environ.get(key)
            os.environ[key] = val

    def tearDown(self):
        for key, val in self._old_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ──────────────────────────────────────────────

    def write_memory(self, rel: str, body: str = "memory body") -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def fill_all_profile_topics(self):
        corpus = " ".join(
            kw[0] for _, _, _, _, kw in muse_call._PROFILE_TOPICS
        )
        self.write_memory("10_Profile/codex.md", corpus)


class TestGapDetection(MuseCallBase):

    def test_empty_vault_all_domains_silent(self):
        gaps = muse_call.find_silent_muses(self.vault)
        kinds = {g["qid"] for g in gaps}
        self.assertEqual(kinds, {
            "silent_muse:10_Profile", "silent_muse:30_Journal",
            "silent_muse:40_Projects", "silent_muse:50_Knowledge",
        })

    def test_domain_with_memory_not_silent(self):
        self.write_memory("30_Journal/2026/260601.md")
        gaps = muse_call.find_silent_muses(self.vault)
        self.assertNotIn("silent_muse:30_Journal", {g["qid"] for g in gaps})

    def test_readme_and_system_dirs_ignored(self):
        self.write_memory("30_Journal/README.md")
        self.write_memory("30_Journal/_archive/old.md")
        gaps = muse_call.find_silent_muses(self.vault)
        self.assertIn("silent_muse:30_Journal", {g["qid"] for g in gaps})

    def test_profile_topic_gaps_skip_when_profile_empty(self):
        # 全空交給 silent_muse，不重複出題
        self.assertEqual(muse_call.find_profile_topic_gaps(self.vault), [])

    def test_profile_topic_gap_found_and_covered(self):
        self.write_memory("10_Profile/about.md", "我的家人：父親與母親。")
        gaps = {g["qid"] for g in muse_call.find_profile_topic_gaps(self.vault)}
        self.assertNotIn("profile_topic:family", gaps)
        self.assertIn("profile_topic:values", gaps)

    def test_temporal_gap_detects_silent_month(self):
        today = datetime(2026, 6, 10)
        self.write_memory("30_Journal/2026/260501.md")   # 2026-05 covered
        self.write_memory("30_Journal/2026/2026-03-15.md")  # 2026-03 covered
        gaps = {g["qid"] for g in
                muse_call.find_temporal_gaps(self.vault, today=today)}
        self.assertIn("temporal_gap:2026-04", gaps)
        self.assertNotIn("temporal_gap:2026-05", gaps)
        self.assertNotIn("temporal_gap:2026-03", gaps)
        self.assertNotIn("temporal_gap:2026-06", gaps)  # 本月不算

    def test_stale_domain_by_mtime(self):
        path = self.write_memory("40_Projects/old_project.md")
        old = (datetime.now() - timedelta(days=90)).timestamp()
        os.utime(path, (old, old))
        gaps = {g["qid"] for g in muse_call.find_stale_domains(self.vault)}
        self.assertIn("stale_domain:40_Projects", gaps)

    def test_fresh_domain_not_stale(self):
        self.write_memory("40_Projects/current.md")
        gaps = {g["qid"] for g in muse_call.find_stale_domains(self.vault)}
        self.assertNotIn("stale_domain:40_Projects", gaps)

    def test_thin_persons_degrade_silently_without_tapestry(self):
        # Tapestry / kuzu 不可用時必須回空，不拋錯。
        # 用 mock 模擬（本機若裝了 kuzu，真實 tapestry 反而可用）。
        from unittest import mock
        broken = mock.Mock()
        broken.get_conn.side_effect = RuntimeError("kuzu unavailable")
        with mock.patch.dict(sys.modules, {"tapestry": broken}):
            self.assertEqual(muse_call.find_thin_persons(), [])


class TestSelection(MuseCallBase):

    def test_count_and_priority(self):
        qs = muse_call.select_questions(count=2, lang="en")
        self.assertEqual(len(qs), 2)
        # 空 vault → silent_muse 優先度最高，應佔滿前兩題
        self.assertTrue(all(q["kind"] == "silent_muse" for q in qs))

    def test_deterministic_per_day(self):
        today = datetime(2026, 6, 10)
        a = [q["qid"] for q in
             muse_call.select_questions(count=5, lang="en", today=today)]
        b = [q["qid"] for q in
             muse_call.select_questions(count=5, lang="en", today=today)]
        self.assertEqual(a, b)

    def test_lang_zh(self):
        qs = muse_call.select_questions(count=1, lang="zh")
        self.assertTrue(any("一" <= ch <= "鿿" for ch in qs[0]["question"]))

    def test_answered_question_enters_cooldown(self):
        qs = muse_call.select_questions(count=1, lang="en")
        qid = qs[0]["qid"]
        muse_call.record(qid, qs[0]["kind"], qs[0]["question"], "answered")
        remaining = {q["qid"] for q in muse_call.select_questions(count=10)}
        self.assertNotIn(qid, remaining)

    def test_skipped_cooldown_expires(self):
        qs = muse_call.select_questions(count=1, lang="en")
        qid = qs[0]["qid"]
        muse_call.record(qid, qs[0]["kind"], qs[0]["question"], "skipped")
        # 冷卻中
        self.assertNotIn(qid, {q["qid"] for q in muse_call.select_questions(count=10)})
        # 偽造 20 天後 → 重新出現
        future = datetime.now() + timedelta(days=20)
        self.assertIn(qid, {q["qid"] for q in
                            muse_call.select_questions(count=10, today=future)})

    def test_answered_is_terminal_over_later_asked(self):
        qs = muse_call.select_questions(count=1, lang="en")
        qid = qs[0]["qid"]
        muse_call.record(qid, qs[0]["kind"], qs[0]["question"], "answered")
        muse_call.record(qid, qs[0]["kind"], qs[0]["question"], "asked")
        # asked(2 天) 過期後，answered(120 天) 的冷卻仍應生效
        future = datetime.now() + timedelta(days=10)
        remaining = {q["qid"] for q in
                     muse_call.select_questions(count=20, today=future)}
        self.assertNotIn(qid, remaining)


class TestAnswerFlow(MuseCallBase):

    def test_submit_answer_writes_spring_and_ledger(self):
        qs = muse_call.select_questions(count=1, lang="en")
        qid = qs[0]["qid"]
        path = muse_call.submit_answer(qid, "I grew up by the sea.")
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("I grew up by the sea.", content)
        self.assertIn('source: "The Call of the Muses"', content)
        self.assertIn("type: journal", content)
        # ledger 記了 answered → 不再被選中
        self.assertNotIn(qid, {q["qid"] for q in muse_call.select_questions(count=20)})

    def test_submit_answer_appends_same_day(self):
        qs = muse_call.select_questions(count=2, lang="en")
        p1 = muse_call.submit_answer(qs[0]["qid"], "answer one")
        p2 = muse_call.submit_answer(qs[1]["qid"], "answer two")
        self.assertEqual(p1, p2)
        content = p1.read_text(encoding="utf-8")
        self.assertIn("answer one", content)
        self.assertIn("answer two", content)
        # frontmatter 只出現一次
        self.assertEqual(content.count('source: "The Call of the Muses"'), 1)

    def test_submit_answer_empty_rejected(self):
        with self.assertRaises(ValueError):
            muse_call.submit_answer("profile_topic:values", "   ")

    def test_submit_answer_unknown_qid(self):
        with self.assertRaises(KeyError):
            muse_call.submit_answer("no_such_kind:nothing", "text")

    def test_submit_answer_falls_back_to_ledger_question(self):
        # 問題已不在缺口中（領域已補上），但 ledger 有 asked 紀錄 → 仍可回答
        muse_call.record("silent_muse:30_Journal", "silent_muse",
                         "What happened today?", "asked")
        self.write_memory("30_Journal/2026/260610.md")  # 缺口消失
        path = muse_call.submit_answer("silent_muse:30_Journal", "A quiet day.")
        self.assertIn("A quiet day.", path.read_text(encoding="utf-8"))


class TestStats(MuseCallBase):

    def test_stats_counts(self):
        qs = muse_call.select_questions(count=2, lang="en")
        muse_call.record(qs[0]["qid"], qs[0]["kind"], qs[0]["question"], "skipped")
        muse_call.submit_answer(qs[1]["qid"], "an answer")
        data = muse_call.stats()
        self.assertEqual(data["counts"]["skipped"], 1)
        self.assertEqual(data["counts"]["answered"], 1)
        self.assertGreater(data["open_gaps"], 0)

    def test_ledger_is_valid_jsonl(self):
        qs = muse_call.select_questions(count=1, lang="en")
        muse_call.record(qs[0]["qid"], qs[0]["kind"], qs[0]["question"], "asked")
        for line in muse_call.ledger_path().read_text(encoding="utf-8").splitlines():
            json.loads(line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
