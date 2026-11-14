"""Path resolution: home / vault / artifacts, the pre-1.5 layout, memory discovery."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from memosyne import paths

_VARS = ("MEMOSYNE_HOME", "MEMOSYNE_VAULT_DIR", "MEMOSYNE_ARTIFACT_DIR", "MEMOSYNE_SPRING_DIR")


class _Home(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        env = {k: v for k, v in os.environ.items() if k not in _VARS}
        env["MEMOSYNE_HOME"] = str(self.home)
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def touch(self, rel: str, text: str = "x") -> Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class TestFreshLayout(_Home):
    def test_defaults_live_under_home(self):
        self.assertEqual(paths.home(), self.home)
        self.assertEqual(paths.data_root(), self.home / "vault")
        self.assertEqual(paths.artifact_dir(), self.home / "vault" / "_artifacts")
        self.assertEqual(paths.spring_dir(), self.home / "spring")
        self.assertEqual(paths.artifact_path("bm25_index"),
                         self.home / "vault" / "_artifacts" / "bm25_index.pkl")

    def test_vault_env_keeps_artifacts_in_the_vault(self):
        with mock.patch.dict(os.environ, {"MEMOSYNE_VAULT_DIR": str(self.home / "v")}):
            self.assertEqual(paths.data_root(), self.home / "v")
            self.assertEqual(paths.artifact_dir(), self.home / "v")

    def test_artifact_env_wins(self):
        env = {"MEMOSYNE_VAULT_DIR": str(self.home / "v"),
               "MEMOSYNE_ARTIFACT_DIR": str(self.home / "a")}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(paths.artifact_path("chroma_db"), self.home / "a" / "chroma_db")

    def test_unknown_artifact_is_an_error(self):
        with self.assertRaises(KeyError):
            paths.artifact_path("no_such_artifact")


class TestLegacyLayout(_Home):
    """A pre-1.5 checkout keeps working in place after upgrading."""

    def setUp(self):
        super().setUp()
        self.vault = self.home / "Personal_Brain_DB" / "_vault"
        self.touch("Personal_Brain_DB/_vault/30_Journal/2025/250101.md")

    def test_vault_is_found_and_artifacts_stay_flat(self):
        self.assertEqual(paths.data_root(), self.vault)
        self.assertEqual(paths.artifact_dir(), self.vault)
        self.assertEqual(paths.artifact_path("chronicle_db"), self.vault / "chronicle.db")

    def test_artifacts_left_next_to_the_old_code_are_honoured(self):
        legacy = self.home / "Personal_Brain_DB" / "00_System"
        (legacy / "chroma_db").mkdir(parents=True)
        self.touch("Personal_Brain_DB/00_System/aletheia_log.jsonl")
        self.assertEqual(paths.artifact_path("chroma_db"), legacy / "chroma_db")
        self.assertEqual(paths.artifact_path("corrections_log"), legacy / "aletheia_log.jsonl")
        # not present there → falls through to the vault
        self.assertEqual(paths.artifact_path("bm25_index"), self.vault / "bm25_index.pkl")

    def test_explicit_env_ignores_the_legacy_location(self):
        (self.home / "Personal_Brain_DB" / "00_System" / "chroma_db").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"MEMOSYNE_ARTIFACT_DIR": str(self.home / "a")}):
            self.assertEqual(paths.artifact_path("chroma_db"), self.home / "a" / "chroma_db")

    def test_empty_legacy_folder_does_not_count(self):
        (self.vault / "30_Journal" / "2025" / "250101.md").unlink()
        for d in sorted(self.vault.rglob("*"), reverse=True):
            d.rmdir()
        self.assertEqual(paths.data_root(), self.home / "vault")


class TestMemoryDiscovery(_Home):
    def test_only_files_inside_domain_folders_are_memories(self):
        v = "vault/"
        keep = [self.touch(v + "30_Journal/2025/250101.md"),
                self.touch(v + "10_Profile/bio.md"),
                self.touch(v + "60_Custom/note.md")]
        for skipped in ["README.md", "stray_note.md", "_digest/timeline.md",
                        "_artifacts/x.md", ".obsidian/y.md", "benchmark_reports/eval.md",
                        "augury_reports/r.md", "logs/l.md", "30_Journal/README.md"]:
            self.touch(v + skipped)
        self.assertEqual(sorted(paths.iter_memory_files()), sorted(keep))

    def test_missing_vault_yields_nothing(self):
        self.assertEqual(list(paths.iter_memory_files()), [])

    def test_relative_path_hides_the_legacy_vault_prefix(self):
        f = self.touch("Personal_Brain_DB/_vault/30_Journal/2025/250101.md")
        brain = self.home / "Personal_Brain_DB"
        self.assertEqual(paths.rel_memory_path(f, brain), "30_Journal/2025/250101.md")
        self.assertEqual(paths.rel_memory_path(f), "30_Journal/2025/250101.md")
        self.assertEqual([p.name for p in paths.iter_memory_files(brain)], ["250101.md"])


if __name__ == "__main__":
    unittest.main()
