"""
Memosyne path resolution — the one place that decides where things live.

Three locations matter:

  home      project directory: holds ``.env``, ``spring/`` and (by default) the vault
  vault     human-readable Markdown memories (the source of truth)
  artifacts derived, rebuildable files: indexes, caches, logs

Resolution order (first match wins):

  home       MEMOSYNE_HOME  →  source checkout root  →  current directory
  vault      MEMOSYNE_VAULT_DIR  →  <home>/Personal_Brain_DB/_vault (pre-1.5 layout)
             →  <home>/vault
  artifacts  MEMOSYNE_ARTIFACT_DIR  →  the vault itself when MEMOSYNE_VAULT_DIR is
             set or the pre-1.5 layout is in use  →  <vault>/_artifacts
  spring     MEMOSYNE_SPRING_DIR  →  <home>/spring

Everything is resolved at call time, so tests and tools can re-point the
environment without reloading modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

PACKAGE_DIR = Path(__file__).resolve().parent

# Pre-1.5 checkouts kept code and data in one tree. These names let an
# existing vault keep working in place after upgrading.
_LEGACY_BRAIN = "Personal_Brain_DB"
_LEGACY_SYSTEM = "00_System"
_LEGACY_VAULT = "_vault"

# Top-level vault entries that never contain memories.
NON_MEMORY_DIRS = frozenset({
    _LEGACY_SYSTEM, "_artifacts", "_eval", "augury_reports", "benchmark_reports",
    "logs", "chroma_db", "aletheia_backup", "flashrank_cache",
})
NON_MEMORY_FILES = frozenset({"README.md", ".cursorrules"})


# ─── Locations ────────────────────────────────────────────────

def source_root() -> Path | None:
    """Repo root when running from a source checkout (editable install), else None."""
    candidate = PACKAGE_DIR.parent.parent
    if PACKAGE_DIR.parent.name == "src" and (candidate / "pyproject.toml").exists():
        return candidate
    return None


def home() -> Path:
    """Project directory holding ``.env``, ``spring/`` and the default vault."""
    env = os.getenv("MEMOSYNE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return source_root() or Path.cwd()


def _legacy_vault() -> Path | None:
    candidate = home() / _LEGACY_BRAIN / _LEGACY_VAULT
    try:
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    except OSError:
        pass
    return None


def data_root() -> Path:
    """Directory that contains the memory domains (10_Profile … 50_Knowledge)."""
    env = os.getenv("MEMOSYNE_VAULT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return _legacy_vault() or home() / "vault"


def artifact_dir() -> Path:
    """Directory for derived, rebuildable files (indexes, caches, logs)."""
    env = os.getenv("MEMOSYNE_ARTIFACT_DIR")
    if env:
        return Path(env).expanduser()
    if os.getenv("MEMOSYNE_VAULT_DIR") or _legacy_vault():
        return data_root()
    return data_root() / "_artifacts"


def spring_dir() -> Path:
    """Drop zone for files waiting to be ingested."""
    env = os.getenv("MEMOSYNE_SPRING_DIR")
    if env:
        return Path(env).expanduser()
    return home() / "spring"


def sample_vault_dir() -> Path | None:
    """The synthetic sample vault; only present in a source checkout."""
    root = source_root()
    if root and (root / "sample_vault").is_dir():
        return root / "sample_vault"
    return None


_env_loaded = False


def load_env() -> None:
    """Load ``<home>/.env`` once. Existing environment variables win."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = home() / ".env"
    if env_file.exists():
        load_dotenv(env_file)


def rel_memory_path(md_file: Path, root: Path | None = None) -> str:
    """Vault-relative path used as the memory's identity in every index."""
    rel = md_file.relative_to(root or data_root()).as_posix()
    # Pre-1.5 layouts could be scanned from one level above the vault.
    if rel.startswith(_LEGACY_VAULT + "/"):
        rel = rel[len(_LEGACY_VAULT) + 1:]
    return rel


def iter_memory_files(root: Path | None = None) -> Iterator[Path]:
    """Yield every memory file in the vault, sorted.

    A memory is a ``.md`` file inside a top-level vault folder. Files at the
    vault root, hidden or underscore-prefixed folders, and report / index
    folders are not memories.
    """
    base = root or data_root()
    if not base.is_dir():
        return
    for md_file in sorted(base.rglob("*.md")):
        parts = Path(rel_memory_path(md_file, base)).parts
        if len(parts) < 2:
            continue
        top = parts[0]
        if top.startswith((".", "_")) or top in NON_MEMORY_DIRS:
            continue
        if md_file.name in NON_MEMORY_FILES:
            continue
        yield md_file


# ─── Artifact registry ────────────────────────────────────────

@dataclass(frozen=True)
class Artifact:
    key: str
    relative_path: str
    kind: str
    privacy: str
    description: str
    # Which install layer produces it: "core", "search" or "labs". Health only
    # insists on artifacts whose layer is actually in use.
    layer: str = "core"
    # Pre-1.5 checkouts kept some artifacts next to the code. When True, that
    # location is honoured if the file is still there.
    legacy_shim: bool = True
    # Allowed to be absent without health complaining: ephemeral markers
    # (consumed by the next run) and opt-in logs.
    ephemeral: bool = False


def _a(key: str, rel: str, kind: str, description: str, **kw) -> Artifact:
    return Artifact(key=key, relative_path=rel, kind=kind, privacy="private",
                    description=description, **kw)


ARTIFACTS: dict[str, Artifact] = {a.key: a for a in [
    _a("bm25_index", "bm25_index.pkl", "derived retrieval index",
       "BM25 keyword index over vault chunks."),
    _a("chronicle_jsonl", "chronicle.jsonl", "append-only log",
       "Access log source of truth; one access event per line.", legacy_shim=False),
    _a("chronicle_db", "chronicle.db", "derived sqlite cache",
       "SQLite cache of the access log, plus the turn ledger."),
    _a("muse_call_ledger", "muse_call_ledger.jsonl", "append-only log",
       "Gap-question ledger: asked / answered / skipped history for cooldowns.",
       legacy_shim=False, ephemeral=True),
    _a("dirty_paths", "dirty_paths.txt", "ephemeral marker",
       "Paths whose vault file changed and whose chunks need refreshing; "
       "consumed by the next index build.", legacy_shim=False, ephemeral=True),
    _a("chroma_db", "chroma_db", "derived vector store",
       "ChromaDB persistent vector index.", layer="search"),
    _a("contextual_cache", "contextual_cache.json", "llm cache",
       "Contextual paragraph notes (The Illumination).", layer="labs", ephemeral=True),
    _a("hyqe_cache", "hyqe_cache.json", "llm cache",
       "Hypothetical questions per chunk (The Triple Echo).", layer="labs", ephemeral=True),
    _a("tapestry_db", "tapestry_db", "derived graph store",
       "Entity graph database (The Tapestry).", layer="labs"),
    _a("muse_centroids", "muse_centroids.json", "derived routing data",
       "Domain-routing centroids built from the vault.", layer="labs"),
    _a("query_log_jsonl", "query_log.jsonl", "append-only log",
       "Captured queries for replay; opt-in via MEMOSYNE_CAPTURE_QUERIES=1.",
       layer="labs", legacy_shim=False, ephemeral=True),
    _a("corrections_log", "aletheia_log.jsonl", "append-only log",
       "Audit trail of manual corrections.", layer="labs", ephemeral=True),
    _a("corrections_backup", "aletheia_backup", "snapshot directory",
       "Pre-correction snapshots used by revert.", layer="labs", ephemeral=True),
    _a("corrections_pending", "aletheia_pending_reembed.json", "ephemeral marker",
       "Corrected paths waiting to be re-embedded.", layer="labs", ephemeral=True),
    _a("naming_log", "naming_log.jsonl", "append-only log",
       "Entity alias merges performed during consolidation.", layer="labs", ephemeral=True),
    _a("ordeal_log", "ordeal_log.jsonl", "append-only log",
       "Contradiction checks performed during consolidation.", layer="labs", ephemeral=True),
    _a("audit_reports", "augury_reports", "report directory",
       "Memory audit reports.", layer="labs", ephemeral=True),
    _a("benchmark_reports", "benchmark_reports", "report directory",
       "Retrieval evaluation reports.", layer="labs", ephemeral=True),
    _a("flashrank_cache", "flashrank_cache", "model cache",
       "Reranker model cache used by the RAG chat.", layer="labs", ephemeral=True),
]}


def _artifact_for(name: str) -> Artifact:
    if name in ARTIFACTS:
        return ARTIFACTS[name]
    for artifact in ARTIFACTS.values():
        if artifact.relative_path == name:
            return artifact
    raise KeyError(f"Unknown Memosyne artifact: {name}")


def artifact_path(name: str) -> Path:
    """Return the path for a known artifact (by key or file name)."""
    artifact = _artifact_for(name)
    explicit = os.getenv("MEMOSYNE_ARTIFACT_DIR") or os.getenv("MEMOSYNE_VAULT_DIR")
    if not explicit and artifact.legacy_shim:
        legacy = home() / _LEGACY_BRAIN / _LEGACY_SYSTEM / artifact.relative_path
        if legacy.exists() or legacy.is_symlink():
            return legacy
    return artifact_dir() / artifact.relative_path


def ensure_parent(path: Path) -> None:
    """Create an artifact's parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)


def artifact_manifest() -> list[dict]:
    """Machine-readable manifest for health checks and docs."""
    rows: list[dict] = []
    for artifact in ARTIFACTS.values():
        path = artifact_path(artifact.key)
        # A bind-mounted artifact that never existed on the host comes back
        # as an empty *directory* (docker-compose creates one). Report the
        # shape so health can tell "missing" from "wrong kind of thing".
        expects_file = bool(Path(artifact.relative_path).suffix)
        rows.append({
            "key": artifact.key,
            "path": str(path),
            "exists": path.exists(),
            "expects_file": expects_file,
            "is_dir": path.is_dir(),
            "kind": artifact.kind,
            "privacy": artifact.privacy,
            "description": artifact.description,
            "layer": artifact.layer,
            "ephemeral": artifact.ephemeral,
        })
    return rows
