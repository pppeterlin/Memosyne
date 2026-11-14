"""The default search path: keyword-only without the search extra, core vs full
profile gating, AI-chat scope, and the index pruning guard."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memosyne import retrieval as R

JOURNAL = """---
type: journal
date: 2025-06-04
---

# Lake week

Spent the whole afternoon sketching herons beside the reservoir with Alex.
"""
CHAT = """---
type: chat
source: gemini
---

## 使用者

Why do herons stand on one leg beside a reservoir?

## Gemini

Herons stand on one leg to conserve body heat while resting near water.
"""


class _Vault(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.art = Path(self._tmp.name) / "art"
        self.art.mkdir()
        self.write("30_Journal/2025/250604.md", JOURNAL)
        self.write("20_AI_Chats/Gemini/herons.md", CHAT)
        self.write("benchmark_reports/eval.md", "# report\n\nherons reservoir herons reservoir\n")
        # BM25 needs a corpus where the query terms are rare, as in a real vault.
        for i, topic in enumerate(["sourdough starter hydration ratios for the weekend bake",
                                   "quarterly budget review and the spreadsheet migration",
                                   "replacing the bicycle chain and tuning the derailleur",
                                   "notes from the pottery class about glazing temperatures",
                                   "planning the autumn vegetable beds and compost rotation",
                                   "debugging the flaky integration test on the build server"]):
            self.write(f"30_Journal/2024/24010{i}.md",
                       f"---\ntype: journal\n---\n\nToday was mostly about {topic}, nothing else.\n")
        self._stack = contextlib.ExitStack()
        for name, value in {
            "BASE": self.vault,
            "BM25_PATH": self.art / "bm25_index.pkl",
            "CTX_CACHE": self.art / "contextual_cache.json",
            "HYQE_CACHE": self.art / "hyqe_cache.json",
            "CHROMA_DIR": self.art / "chroma_db",
            "_ctx_cache": None,
        }.items():
            self._stack.enter_context(mock.patch.object(R, name, value, create=True))
        self._stack.enter_context(mock.patch.object(R, "_hyqe_cache", None, create=True))
        self._stack.enter_context(mock.patch.object(R, "dense_available", lambda: False))
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("MEMOSYNE_SEARCH", "MEMOSYNE_INDEX", "MEMOSYNE_WALK"))}
        env["MEMOSYNE_ARTIFACT_DIR"] = str(self.art)
        self._stack.enter_context(mock.patch.dict(os.environ, env, clear=True))

    def tearDown(self):
        self._stack.close()
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def build(self) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            R.build_index()
        return out.getvalue()

    def paths(self, query: str, **kw) -> list[str]:
        return [r["path"] for r in R.search(query, top_k=5, record_access=False, **kw)]


class TestKeywordOnlyInstall(_Vault):
    def test_build_and_search_work_without_the_search_extra(self):
        log = self.build()
        self.assertIn("keyword index only", log)
        self.assertTrue((self.art / "bm25_index.pkl").exists())
        self.assertFalse((self.art / "chroma_db").exists())
        self.assertEqual(self.paths("herons reservoir"), ["30_Journal/2025/250604.md"])

    def test_reports_and_root_files_are_never_indexed(self):
        self.write("stray.md", "herons reservoir\n")
        self.build()
        indexed = {c["meta"]["path"] for c in R.collect_all_chunks()}
        self.assertEqual({p for p in indexed if not p.startswith("30_Journal/2024/")},
                         {"30_Journal/2025/250604.md", "20_AI_Chats/Gemini/herons.md"})

    def test_search_before_any_index_returns_nothing(self):
        self.assertEqual(self.paths("herons"), [])

    def test_keyword_index_is_loaded_once_and_reloaded_when_rebuilt(self):
        self.build()
        with mock.patch.object(R.pickle, "loads", wraps=R.pickle.loads) as loads:
            self.paths("herons reservoir")
            self.paths("herons reservoir")
            self.assertEqual(loads.call_count, 1)
            self.write("30_Journal/2025/250901.md",
                       "---\ntype: journal\n---\n\nAn otter appeared beside the reservoir at dusk today.\n")
            self.build()
            self.assertIn("30_Journal/2025/250901.md", self.paths("otter dusk"))
            self.assertEqual(loads.call_count, 2)


class TestChatScope(_Vault):
    def setUp(self):
        super().setUp()
        self.build()

    def test_chats_are_out_of_scope_by_default(self):
        self.assertNotIn("20_AI_Chats/Gemini/herons.md", self.paths("herons one leg"))

    def test_chats_join_on_request(self):
        self.assertIn("20_AI_Chats/Gemini/herons.md",
                      self.paths("herons one leg", include_chats=True))
        with mock.patch.dict(os.environ, {"MEMOSYNE_SEARCH_CHATS": "1"}):
            self.assertIn("20_AI_Chats/Gemini/herons.md", self.paths("herons one leg"))

    def test_explicit_type_filter_overrides_the_scope(self):
        self.assertEqual(self.paths("herons one leg", doc_type="chat"),
                         ["20_AI_Chats/Gemini/herons.md"])

    def test_chats_can_be_left_out_of_the_index_entirely(self):
        with mock.patch.dict(os.environ, {"MEMOSYNE_INDEX_CHATS": "0"}):
            self.build()
        self.assertEqual(self.paths("herons one leg", include_chats=True),
                         ["30_Journal/2025/250604.md"])


class TestProfiles(_Vault):
    def setUp(self):
        super().setUp()
        self.build()

    def test_core_profile_never_touches_labs_stages(self):
        boom = mock.Mock(side_effect=AssertionError("labs stage ran under the core profile"))
        with mock.patch.object(R, "search_graph", boom), \
             mock.patch("memosyne.access_log.actr_rerank", boom):
            self.assertTrue(self.paths("herons reservoir"))
            self.assertTrue(self.paths("herons reservoir", profile="core"))
        boom.assert_not_called()

    def test_full_profile_runs_graph_and_actr(self):
        graph = mock.Mock(return_value=[])
        actr = mock.Mock(side_effect=lambda results, *a, **k: results)
        with mock.patch.object(R, "search_graph", graph), \
             mock.patch("memosyne.access_log.actr_rerank", actr):
            self.paths("herons reservoir", profile="full")
            with mock.patch.dict(os.environ, {"MEMOSYNE_SEARCH_PROFILE": "full"}):
                self.paths("herons reservoir")
        self.assertEqual(graph.call_count, 2)
        self.assertEqual(actr.call_count, 2)

    def test_unknown_profile_falls_back_to_core(self):
        self.assertEqual(R.search_profile("nonsense"), "core")
        with mock.patch.dict(os.environ, {"MEMOSYNE_SEARCH_PROFILE": "FULL "}):
            self.assertEqual(R.search_profile(), "full")


class _FakeCollection:
    def __init__(self):
        self.deleted: list[str] = []

    def delete(self, ids):
        self.deleted += list(ids)


class TestPruneGuard(unittest.TestCase):
    def prune(self, existing: set[str], wanted: set[str]):
        col = _FakeCollection()
        with contextlib.redirect_stdout(io.StringIO()):
            n = R._prune_stale_chunks(col, existing, wanted)
        return n, col.deleted

    def test_a_few_stale_chunks_are_dropped(self):
        existing = {f"a::para{i}" for i in range(10)} | {"gone::para0"}
        n, deleted = self.prune(existing, existing - {"gone::para0"})
        self.assertEqual((n, deleted), (1, ["gone::para0"]))

    def test_nothing_stale_is_a_no_op(self):
        self.assertEqual(self.prune({"a", "b"}, {"a", "b", "c"}), (0, []))

    def test_an_empty_vault_never_wipes_the_index(self):
        self.assertEqual(self.prune({"a", "b", "c"}, set()), (0, []))

    def test_a_mostly_missing_vault_is_refused(self):
        existing = {f"x{i}" for i in range(10)}
        self.assertEqual(self.prune(existing, {"x0", "x1", "x2"}), (0, []))


if __name__ == "__main__":
    unittest.main()
