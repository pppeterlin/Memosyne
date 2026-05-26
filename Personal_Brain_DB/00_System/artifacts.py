#!/usr/bin/env python3
"""
Memosyne artifact registry.

v0.3 keeps runtime paths explicit:
- private portable artifacts live in Personal_Brain_DB/_vault by default
- legacy 00_System symlink shims remain valid for existing tools
- local bulky caches can stay under 00_System and out of git
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent
BRAIN_DIR = SYSTEM_DIR.parent
REPO_ROOT = BRAIN_DIR.parent

VAULT_DIR = Path(os.getenv("MEMOSYNE_VAULT_DIR", BRAIN_DIR / "_vault")).expanduser()
ARTIFACT_DIR = Path(os.getenv("MEMOSYNE_ARTIFACT_DIR", VAULT_DIR)).expanduser()


def data_root() -> Path:
    """
    Return the directory that contains the muse subdirectories
    (10_Profile, 20_AI_Chats, 30_Journal, 40_Projects, 50_Knowledge).

    When MEMOSYNE_VAULT_DIR is set, the override IS the data root: scripts
    scan and write under that path directly. This is the path used by
    sample_vault and any external data layout.

    When no override is set, the data root is the legacy Personal_Brain_DB
    layout, where muse subdirectories are symlinks into _vault/<muse>/.
    """
    env = os.getenv("MEMOSYNE_VAULT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return BRAIN_DIR


@dataclass(frozen=True)
class Artifact:
    key: str
    relative_path: str
    kind: str
    privacy: str
    description: str
    legacy_shim: bool = True


ARTIFACTS: dict[str, Artifact] = {
    "chronicle_jsonl": Artifact(
        key="chronicle_jsonl",
        relative_path="chronicle.jsonl",
        kind="append-only log",
        privacy="private",
        description="The Chronicle of Mneme source log; one access event per JSONL line.",
        legacy_shim=False,
    ),
    "chronicle_db": Artifact(
        key="chronicle_db",
        relative_path="chronicle.db",
        kind="derived sqlite cache",
        privacy="private",
        description="SQLite acceleration cache derived from Chronicle JSONL events.",
    ),
    "bm25_index": Artifact(
        key="bm25_index",
        relative_path="bm25_index.pkl",
        kind="derived retrieval index",
        privacy="private",
        description="BM25 sparse index for private vault chunks.",
    ),
    "contextual_cache": Artifact(
        key="contextual_cache",
        relative_path="contextual_cache.json",
        kind="llm cache",
        privacy="private",
        description="The Illumination contextual notes cache.",
    ),
    "hyqe_cache": Artifact(
        key="hyqe_cache",
        relative_path="hyqe_cache.json",
        kind="llm cache",
        privacy="private",
        description="The Triple Echo hypothetical question cache.",
    ),
    "tapestry_db": Artifact(
        key="tapestry_db",
        relative_path="tapestry_db",
        kind="derived graph store",
        privacy="private",
        description="The Tapestry Kuzu graph database.",
    ),
    "chroma_db": Artifact(
        key="chroma_db",
        relative_path="chroma_db",
        kind="derived vector store",
        privacy="private",
        description="ChromaDB persistent vector index.",
    ),
    "muse_centroids": Artifact(
        key="muse_centroids",
        relative_path="muse_centroids.json",
        kind="derived routing data",
        privacy="private",
        description="Muse routing centroids built from the private vault.",
    ),
    "query_log_jsonl": Artifact(
        key="query_log_jsonl",
        relative_path="query_log.jsonl",
        kind="append-only log",
        privacy="private",
        description="The Augury Replay capture log; opt-in via "
                    "MEMOSYNE_CAPTURE_QUERIES=1 (default off).",
        legacy_shim=False,
    ),
    "dirty_paths": Artifact(
        key="dirty_paths",
        relative_path="dirty_paths.txt",
        kind="ephemeral marker",
        privacy="private",
        description="v0.6 turn-aware update marker — paths whose vault "
                    "file changed and whose vector chunks need refresh. "
                    "Consumed and cleared by vectorize.build_index().",
        legacy_shim=False,
    ),
}


def _artifact_for(name: str) -> Artifact:
    if name in ARTIFACTS:
        return ARTIFACTS[name]
    for artifact in ARTIFACTS.values():
        if artifact.relative_path == name:
            return artifact
    raise KeyError(f"Unknown Memosyne artifact: {name}")


def artifact_path(name: str) -> Path:
    """
    Return the path for a known artifact.

    Existing 00_System symlink shims are preferred for backwards compatibility.
    New artifacts go directly to ARTIFACT_DIR, which defaults to _vault.
    """
    artifact = _artifact_for(name)
    if os.getenv("MEMOSYNE_ARTIFACT_DIR") or os.getenv("MEMOSYNE_VAULT_DIR"):
        return ARTIFACT_DIR / artifact.relative_path
    shim = SYSTEM_DIR / artifact.relative_path
    if artifact.legacy_shim and (shim.exists() or shim.is_symlink()):
        return shim
    return ARTIFACT_DIR / artifact.relative_path


def ensure_parent(path: Path) -> None:
    """Create an artifact parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)


def artifact_manifest() -> list[dict]:
    """Return a small machine-readable manifest for health checks and docs."""
    rows: list[dict] = []
    for artifact in ARTIFACTS.values():
        path = artifact_path(artifact.key)
        rows.append({
            "key": artifact.key,
            "path": str(path),
            "exists": path.exists(),
            "kind": artifact.kind,
            "privacy": artifact.privacy,
            "description": artifact.description,
        })
    return rows
