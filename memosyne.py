#!/usr/bin/env python3
"""
Memosyne command surface.

v0.3 starts as a conservative wrapper around the existing ritual scripts. The
goal is one predictable entry point without forcing an internal refactor first.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRAIN_DIR = ROOT / "Personal_Brain_DB"
SYSTEM_DIR = BRAIN_DIR / "00_System"
VAULT_DIR = BRAIN_DIR / "_vault"
SPRING_DIR = ROOT / "spring"
PYTHON = sys.executable


def _system_script(name: str) -> Path:
    return SYSTEM_DIR / name


def _run_script(name: str, args: list[str]) -> int:
    script = _system_script(name)
    if not script.exists():
        print(f"[fail] missing script: {script}")
        return 1
    cmd = [PYTHON, str(script), *args]
    return subprocess.call(cmd, cwd=str(ROOT))


def _add_passthrough(subparsers, name: str, help_text: str, script: str, fixed: list[str] | None = None):
    parser = subparsers.add_parser(name, help=help_text)
    parser.set_defaults(passthrough_script=script, passthrough_fixed=fixed or [])
    return parser


def _status(ok: bool | None, label: str, detail: str = "") -> str:
    tag = "ok" if ok is True else "warn" if ok is None else "fail"
    suffix = f" {detail}" if detail else ""
    return f"[{tag}] {label}{suffix}"


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _directory_has_entries(path: Path) -> bool:
    try:
        return path.exists() and any(path.iterdir())
    except OSError:
        return False


def _check_secret_files() -> tuple[bool | None, str]:
    public_secret_names = ["openrouter-key", "openrouter.py", "ANTHROPIC_API_KEY", ".env"]
    present = [name for name in public_secret_names if (ROOT / name).exists()]
    if not present:
        return True, "no root-level secret files detected"
    return None, "root-level private files present; keep them out of public release: " + ", ".join(present)


def cmd_health(_: argparse.Namespace) -> int:
    sys.path.insert(0, str(SYSTEM_DIR))
    from artifacts import artifact_manifest

    failures = 0
    checks: list[tuple[bool | None, str, str]] = []

    checks.append((sys.version_info >= (3, 10), f"Python {sys.version_info.major}.{sys.version_info.minor}", "requires 3.10+"))
    checks.append((ROOT.exists(), "repo root", str(ROOT)))
    checks.append((SYSTEM_DIR.exists(), "system directory", str(SYSTEM_DIR)))
    checks.append((SPRING_DIR.exists(), "The Spring", str(SPRING_DIR)))
    checks.append((VAULT_DIR.exists(), "The Vault", str(VAULT_DIR)))

    for module in ["chromadb", "rank_bm25", "kuzu", "mcp", "sentence_transformers"]:
        checks.append((_module_available(module), f"import {module}", ""))

    chroma_dir = SYSTEM_DIR / "chroma_db"
    checks.append((True if _directory_has_entries(chroma_dir) else None, "Chroma DB", str(chroma_dir)))

    for artifact in artifact_manifest():
        expected = artifact["key"] in {
            "chronicle_jsonl",
            "chronicle_db",
            "bm25_index",
            "contextual_cache",
            "hyqe_cache",
            "tapestry_db",
            "muse_centroids",
        }
        exists = bool(artifact["exists"])
        checks.append((exists if expected else None, artifact["key"], artifact["path"]))

    secret_ok, secret_detail = _check_secret_files()
    checks.append((secret_ok, "public secret hygiene", secret_detail))

    print("Memosyne health")
    print("The waters are examined before the rite.\n")
    for ok, label, detail in checks:
        print(_status(ok, label, detail))
        if ok is False:
            failures += 1

    if failures:
        print(f"\nThe Oracle found {failures} blocking issue(s).")
        return 1
    print("\nNothing lost to Lethe.")
    return 0


def cmd_init(_: argparse.Namespace) -> int:
    for path in [SPRING_DIR, VAULT_DIR, SYSTEM_DIR]:
        path.mkdir(parents=True, exist_ok=True)
    print("The Spring, Vault, and System paths are present.")
    return 0


def cmd_search(ns: argparse.Namespace) -> int:
    args = ["--query", ns.query, "--top", str(ns.top)]
    if ns.type:
        args.extend(["--type", ns.type])
    return _run_script("vectorize.py", args)


def cmd_mcp(ns: argparse.Namespace) -> int:
    if ns.check:
        sys.path.insert(0, str(SYSTEM_DIR))
        try:
            import mcp_server  # noqa: F401
        except Exception as exc:
            print(f"[fail] MCP server import failed: {exc}")
            return 1
        print("[ok] MCP server imports successfully")
        return 0
    return _run_script("mcp_server.py", [])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memosyne",
        description="Memosyne — daily command surface for the personal memory vault",
    )
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="ensure core directories exist")
    init.set_defaults(func=cmd_init)

    health = subparsers.add_parser("health", help="check runtime and artifact health")
    health.set_defaults(func=cmd_health)

    search = subparsers.add_parser("search", help="search memories")
    search.add_argument("query")
    search.add_argument("--top", type=int, default=5)
    search.add_argument("--type", default="")
    search.set_defaults(func=cmd_search)

    _add_passthrough(subparsers, "ingest", "run The Spring Ritual", "ingest.py")
    _add_passthrough(subparsers, "rebuild", "rebuild retrieval indexes", "vectorize.py", ["--rebuild"])
    _add_passthrough(subparsers, "slumber", "run The Rite of Slumber", "slumber.py")
    _add_passthrough(subparsers, "chronicle", "inspect The Chronicle of Mneme", "mneme_weight.py")
    _add_passthrough(subparsers, "tapestry", "inspect or rebuild The Tapestry", "tapestry.py")
    _add_passthrough(subparsers, "correct", "run Aletheia correction tools", "aletheia.py")

    mcp = subparsers.add_parser("mcp", help="run or check the MCP server")
    mcp.add_argument("--check", action="store_true", help="import-check the MCP server without starting it")
    mcp.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns, extra = parser.parse_known_args(argv)
    if not hasattr(ns, "func"):
        if hasattr(ns, "passthrough_script"):
            return _run_script(ns.passthrough_script, [*ns.passthrough_fixed, *extra])
        parser.print_help()
        return 0
    if extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return int(ns.func(ns))


if __name__ == "__main__":
    raise SystemExit(main())
