"""MCP tool behaviour that does not need a running server."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memosyne import ingest, mcp_server


class TestSearchMemoryAccessRecording(unittest.TestCase):
    def test_each_result_is_recorded_once_as_an_mcp_access(self):
        results = [{"path": "30_Journal/2025/250604.md", "score": 0.5, "type": "journal"}]
        with mock.patch("memosyne.retrieval.search", return_value=results) as search, \
             mock.patch("memosyne.access_log.record_access") as record:
            text = mcp_server.search_memory("herons", top_k=3, include_chats=True)
        self.assertIn("30_Journal/2025/250604.md", text)
        self.assertIs(search.call_args.kwargs["record_access"], False)
        self.assertIs(search.call_args.kwargs["include_chats"], True)
        record.assert_called_once_with(["30_Journal/2025/250604.md"], source="mcp_search")


class TestReadFileSandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = root / "vault"
        (self.vault / "30_Journal").mkdir(parents=True)
        (self.vault / "30_Journal" / "a.md").write_text("inside", encoding="utf-8")
        (root / "vault-secrets").mkdir()                       # sibling sharing the prefix
        (root / "vault-secrets" / "key.md").write_text("outside", encoding="utf-8")
        self._base = mock.patch.object(mcp_server, "BASE", self.vault)
        self._base.start()
        self._rec = mock.patch("memosyne.access_log.record_access")
        self._rec.start()

    def tearDown(self):
        self._rec.stop()
        self._base.stop()
        self._tmp.cleanup()

    def test_reads_files_inside_the_vault(self):
        self.assertEqual(mcp_server.read_file("30_Journal/a.md"), "inside")

    def test_refuses_traversal_and_prefix_siblings(self):
        for path in ("../vault-secrets/key.md", "30_Journal/../../vault-secrets/key.md",
                     str(self.vault.parent / "vault-secrets" / "key.md")):
            self.assertNotIn("outside", mcp_server.read_file(path), path)

    def test_missing_file_is_reported_not_raised(self):
        self.assertIn("不存在", mcp_server.read_file("30_Journal/nope.md"))


class TestConflictWarning(unittest.TestCase):
    def test_vault_outside_home_does_not_crash_the_warning(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as elsewhere, \
             mock.patch.object(ingest, "ROOT", Path(home)):
            inside, outside = Path(home) / "vault" / "a.md", Path(elsewhere) / "a.md"
            self.assertEqual(ingest._display_path(inside), Path("vault/a.md"))
            self.assertEqual(ingest._display_path(outside), outside)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ingest._warn_conflict(Path("spring/a.md"), outside, "sha256:" + "a" * 64,
                                      "sha256:" + "b" * 64)
            self.assertIn(str(outside), out.getvalue())


if __name__ == "__main__":
    unittest.main()
