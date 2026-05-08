#!/usr/bin/env python3
"""
Memosyne command surface.

v0.3 starts as a conservative wrapper around the existing ritual scripts. The
goal is one predictable entry point without forcing an internal refactor first.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRAIN_DIR = ROOT / "Personal_Brain_DB"
SYSTEM_DIR = BRAIN_DIR / "00_System"
VAULT_DIR = BRAIN_DIR / "_vault"
SPRING_DIR = ROOT / "spring"
PYTHON = sys.executable


@dataclass
class HealthCheck:
    status: str
    label: str
    detail: str = ""
    hint: str = ""

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "label": self.label,
            "detail": self.detail,
            "hint": self.hint,
        }


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


def _status_line(check: HealthCheck) -> str:
    tag = check.status
    suffix = f" {check.detail}" if check.detail else ""
    hint = f"\n       next: {check.hint}" if check.hint and check.status != "ok" else ""
    return f"[{tag}] {check.label}{suffix}{hint}"


def _check(ok: bool | None, label: str, detail: str = "", hint: str = "") -> HealthCheck:
    tag = "ok" if ok is True else "warn" if ok is None else "fail"
    return HealthCheck(tag, label, detail, hint)


def _command_output(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _ollama_available() -> tuple[bool | None, str]:
    url = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            return response.status == 200, url
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, url


def _submodule_state() -> tuple[bool | None, str]:
    code, output = _command_output(["git", "submodule", "status", "--", "Personal_Brain_DB/_vault"])
    if code != 0:
        return None, output or "git submodule status unavailable"
    if output.startswith("-"):
        return False, output
    if output.startswith("+"):
        return None, output
    return True, output


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


def _collect_health_checks() -> list[HealthCheck]:
    sys.path.insert(0, str(SYSTEM_DIR))
    from artifacts import artifact_manifest

    checks: list[HealthCheck] = []

    checks.append(_check(
        sys.version_info >= (3, 10),
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        f"executable={sys.executable}; requires 3.10+",
        "Activate the project virtualenv, then rerun: python memosyne.py health",
    ))
    checks.append(_check(ROOT.exists(), "repo root", str(ROOT)))
    checks.append(_check(SYSTEM_DIR.exists(), "system directory", str(SYSTEM_DIR)))
    checks.append(_check(
        SPRING_DIR.exists(),
        "The Spring",
        str(SPRING_DIR),
        "Run: python memosyne.py init",
    ))
    checks.append(_check(
        VAULT_DIR.exists(),
        "The Vault",
        str(VAULT_DIR),
        "Initialize or fetch the private vault submodule before ingest/search.",
    ))

    for module in ["chromadb", "rank_bm25", "kuzu", "mcp", "sentence_transformers"]:
        checks.append(_check(
            _module_available(module),
            f"import {module}",
            "",
            "Install runtime deps: pip install -r Personal_Brain_DB/00_System/requirements.txt",
        ))

    chroma_dir = SYSTEM_DIR / "chroma_db"
    checks.append(_check(
        True if _directory_has_entries(chroma_dir) else None,
        "Chroma DB",
        str(chroma_dir),
        "Run: python memosyne.py rebuild",
    ))

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
        checks.append(_check(
            exists if expected else None,
            artifact["key"],
            artifact["path"],
            "Run the related rebuild command or restore the private artifact from backup.",
        ))

    submodule_ok, submodule_detail = _submodule_state()
    checks.append(_check(
        submodule_ok,
        "vault submodule",
        submodule_detail,
        "Run: git submodule update --init --recursive",
    ))

    ollama_ok, ollama_detail = _ollama_available()
    checks.append(_check(
        ollama_ok,
        "Ollama API",
        ollama_detail,
        "Start Ollama before enrichment, contextualization, HyQE, or local chat.",
    ))

    secret_ok, secret_detail = _check_secret_files()
    checks.append(_check(
        secret_ok,
        "public secret hygiene",
        secret_detail,
        "Keep machine-specific secrets in ignored local files; do not publish them.",
    ))

    return checks


def cmd_health(ns: argparse.Namespace) -> int:
    checks = _collect_health_checks()
    failures = sum(1 for check in checks if check.failed)
    warnings = sum(1 for check in checks if check.status == "warn")

    if ns.json:
        print(json.dumps({
            "status": "fail" if failures else "warn" if warnings else "ok",
            "failures": failures,
            "warnings": warnings,
            "checks": [check.as_dict() for check in checks],
        }, ensure_ascii=False, indent=2))
        return 1 if failures else 0

    print("Memosyne health")
    print("The waters are examined before the rite.\n")
    for check in checks:
        print(_status_line(check))

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
    if ns.no_record_access:
        args.append("--no-record-access")
    return _run_script("vectorize.py", args)


def cmd_mcp(ns: argparse.Namespace) -> int:
    if ns.print_config:
        config = {
            "mcpServers": {
                ns.name: {
                    "command": sys.executable,
                    "args": [str(_system_script("mcp_server.py"))],
                }
            }
        }
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return 0
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
    health.add_argument("--json", action="store_true", help="emit machine-readable health results")
    health.set_defaults(func=cmd_health)

    search = subparsers.add_parser("search", help="search memories")
    search.add_argument("query")
    search.add_argument("--top", type=int, default=5)
    search.add_argument("--type", default="")
    search.add_argument(
        "--no-record-access",
        action="store_true",
        help="do not write this search to the Chronicle access log",
    )
    search.set_defaults(func=cmd_search)

    _add_passthrough(subparsers, "ingest", "run The Spring Ritual", "ingest.py")
    _add_passthrough(subparsers, "rebuild", "rebuild retrieval indexes", "vectorize.py", ["--rebuild"])
    _add_passthrough(subparsers, "slumber", "run The Rite of Slumber", "slumber.py")
    _add_passthrough(subparsers, "chronicle", "inspect The Chronicle of Mneme", "mneme_weight.py")
    _add_passthrough(subparsers, "tapestry", "inspect or rebuild The Tapestry", "tapestry.py")
    _add_passthrough(subparsers, "correct", "run Aletheia correction tools", "aletheia.py")

    mcp = subparsers.add_parser("mcp", help="run or check the MCP server")
    mcp.add_argument("--check", action="store_true", help="import-check the MCP server without starting it")
    mcp.add_argument("--print-config", action="store_true", help="print an MCP client config snippet")
    mcp.add_argument("--name", default="memosyne", help="MCP server name for --print-config")
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
