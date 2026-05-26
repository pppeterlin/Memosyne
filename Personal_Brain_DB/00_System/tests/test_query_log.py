"""Unit tests for The Augury Replay capture (query_log.py).

純函式 / 單檔 I/O 測試，不需要 chronicle / chromadb / kuzu。

執行：
    python3 -m unittest tests.test_query_log -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import query_log as ql  # noqa: E402


class TestCaptureGate(unittest.TestCase):

    def setUp(self):
        # 清乾淨 env，把 override 重設
        os.environ.pop("MEMOSYNE_CAPTURE_QUERIES", None)
        ql.set_capture_override(None)

    def tearDown(self):
        os.environ.pop("MEMOSYNE_CAPTURE_QUERIES", None)
        ql.set_capture_override(None)

    def test_default_disabled(self):
        self.assertFalse(ql.is_capture_enabled())

    def test_env_var_enables(self):
        os.environ["MEMOSYNE_CAPTURE_QUERIES"] = "1"
        self.assertTrue(ql.is_capture_enabled())

    def test_env_var_true_string(self):
        for val in ["true", "True", "yes", "on", "TRUE"]:
            os.environ["MEMOSYNE_CAPTURE_QUERIES"] = val
            self.assertTrue(ql.is_capture_enabled(), f"failed for {val!r}")

    def test_env_var_falsey_keeps_disabled(self):
        for val in ["0", "false", "no", "off", ""]:
            os.environ["MEMOSYNE_CAPTURE_QUERIES"] = val
            self.assertFalse(ql.is_capture_enabled(), f"failed for {val!r}")

    def test_programmatic_override(self):
        ql.set_capture_override(True)
        self.assertTrue(ql.is_capture_enabled())
        ql.set_capture_override(None)
        self.assertFalse(ql.is_capture_enabled())


class TestScrub(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MEMOSYNE_QUERY_SCRUB_TERMS", None)

    def tearDown(self):
        os.environ.pop("MEMOSYNE_QUERY_SCRUB_TERMS", None)

    def test_email_scrubbed(self):
        text, meta = ql.scrub("contact alice@example.com about it")
        self.assertNotIn("alice@example.com", text)
        self.assertIn("<email>", text)
        self.assertEqual(meta.get("emails"), 1)

    def test_phone_scrubbed(self):
        text, meta = ql.scrub("call 0912-345-678 or +1 555-123-4567")
        self.assertNotIn("0912-345-678", text)
        self.assertNotIn("555-123-4567", text)
        self.assertGreaterEqual(meta.get("phones", 0), 1)

    def test_long_token_scrubbed(self):
        token = "sk_test_aBcDeFgHiJkLmNoPqRsTuVwXyZ_long"
        text, meta = ql.scrub(f"my token is {token} please")
        self.assertNotIn(token, text)
        self.assertIn("<token>", text)
        self.assertEqual(meta.get("tokens"), 1)

    def test_no_pii_returns_clean(self):
        text, meta = ql.scrub("how do I water the plants")
        self.assertEqual(text, "how do I water the plants")
        self.assertEqual(meta, {})

    def test_custom_terms_scrubbed(self):
        os.environ["MEMOSYNE_QUERY_SCRUB_TERMS"] = "Project Phoenix,secret-internal-name"
        text, meta = ql.scrub("update on Project Phoenix and secret-internal-name today")
        self.assertNotIn("Project Phoenix", text)
        self.assertNotIn("secret-internal-name", text)
        self.assertEqual(meta.get("custom"), 2)

    def test_meta_omits_zeros(self):
        """Counts of 0 should not appear in meta to keep JSON tidy."""
        _, meta = ql.scrub("plain text")
        for k, v in meta.items():
            self.assertGreater(v, 0)


class TestRecordAndIter(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        self.tmp.close()
        self.path = Path(self.tmp.name)
        self.path.unlink()  # 確保檔不存在
        # 把模組級 path 改到 tmp
        self._orig_path = ql.QUERY_LOG_PATH
        ql.QUERY_LOG_PATH = self.path
        ql.set_capture_override(True)

    def tearDown(self):
        ql.QUERY_LOG_PATH = self._orig_path
        ql.set_capture_override(None)
        if self.path.exists():
            self.path.unlink()

    def test_record_disabled_returns_false(self):
        ql.set_capture_override(False)
        ok = ql.record_query("hi", ["a.md"])
        self.assertFalse(ok)
        self.assertFalse(self.path.exists())

    def test_record_writes_event(self):
        ok = ql.record_query("how is alice", ["30_Journal/x.md"],
                             top_k=5, latency_ms=120, source="search")
        self.assertTrue(ok)
        lines = self.path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["schema"], ql.SCHEMA)
        self.assertEqual(event["retrieved_paths"], ["30_Journal/x.md"])
        self.assertEqual(event["top_k"], 5)
        self.assertEqual(event["latency_ms"], 120)
        self.assertEqual(event["source"], "search")
        # ts is parseable
        datetime.fromisoformat(event["ts"])

    def test_record_scrubs_pii(self):
        ql.record_query("call alice@example.com about it", ["x.md"])
        line = self.path.read_text().strip()
        event = json.loads(line)
        self.assertNotIn("alice@example.com", event["query"])
        self.assertIn("query_scrub_meta", event)
        self.assertEqual(event["query_scrub_meta"]["emails"], 1)

    def test_iter_events_filters_source(self):
        ql.record_query("a", ["x.md"], source="search")
        ql.record_query("b", ["y.md"], source="mcp")
        ql.record_query("c", ["z.md"], source="search")

        searches = list(ql.iter_events(sources=["search"]))
        mcps = list(ql.iter_events(sources=["mcp"]))
        self.assertEqual(len(searches), 2)
        self.assertEqual(len(mcps), 1)

    def test_export_emits_ndjson(self):
        ql.record_query("a", ["x.md"], source="search")
        ql.record_query("b", ["y.md"], source="search")
        buf = io.StringIO()
        n = ql.export_ndjson(buf)
        self.assertEqual(n, 2)
        lines = buf.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 2)
        # Each is valid JSON with the schema
        for ln in lines:
            self.assertEqual(json.loads(ln)["schema"], ql.SCHEMA)

    def test_iter_events_no_log_yields_nothing(self):
        # path does not exist
        events = list(ql.iter_events())
        self.assertEqual(events, [])


class TestParseSince(unittest.TestCase):

    def test_days(self):
        ts = ql.parse_since("7d")
        self.assertLess(ts, datetime.now())
        # 約 7 天前（容差 1 分鐘）
        self.assertGreater(ts, datetime.now() - timedelta(days=7, minutes=1))

    def test_hours(self):
        ts = ql.parse_since("24h")
        self.assertLess(ts, datetime.now())
        self.assertGreater(ts, datetime.now() - timedelta(hours=24, minutes=1))

    def test_minutes(self):
        ts = ql.parse_since("30m")
        self.assertLess(ts, datetime.now())

    def test_iso(self):
        ts = ql.parse_since("2024-01-01T00:00:00")
        self.assertEqual(ts.year, 2024)
        self.assertEqual(ts.month, 1)

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            ql.parse_since("garbage")


class TestReplay(unittest.TestCase):
    """
    Replay correctness is path-level by design (chunk_id is internal accounting
    that v0.6 will change). These tests pin that contract without needing the
    full vectorize import — we exercise the loader + jaccard helpers and the
    empty-baseline short circuit.
    """

    def test_load_baseline_skips_malformed(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
            fh.write('{"schema": "wrong"}\n')
            fh.write("not json\n")
            fh.write('{"schema": "memosyne.query_log.v1", "query": "ok",'
                     ' "retrieved_paths": ["a.md"]}\n')
            path = Path(fh.name)
        try:
            events = ql._load_baseline(path)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["query"], "ok")
        finally:
            path.unlink(missing_ok=True)

    def test_load_baseline_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            ql._load_baseline(Path("/tmp/nonexistent-baseline-xyz.jsonl"))

    def test_jaccard_helper(self):
        self.assertEqual(ql._jaccard(set(), set()), 1.0)
        self.assertEqual(ql._jaccard({"a"}, {"a"}), 1.0)
        self.assertEqual(ql._jaccard({"a", "b"}, {"a"}), 0.5)
        self.assertEqual(ql._jaccard({"a"}, {"b"}), 0.0)

    def test_replay_empty_baseline_short_circuits(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
            fh.write("")
            path = Path(fh.name)
        try:
            # Empty baseline must not even attempt to import vectorize.
            report = ql.replay(path)
            self.assertEqual(report["n_queries"], 0)
            self.assertIsNone(report["mean_jaccard_at_k"])
            self.assertEqual(report["regressions"], [])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
