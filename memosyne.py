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


def _run_script(name: str, args: list[str],
                env_overrides: dict[str, str] | None = None) -> int:
    script = _system_script(name)
    if not script.exists():
        print(f"[fail] missing script: {script}")
        return 1
    cmd = [PYTHON, str(script), *args]
    env = {**os.environ, **env_overrides} if env_overrides else None
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


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


def _embedding_backend_check() -> HealthCheck:
    """
    v0.8 WS3 — health 涵蓋 remote embedding endpoint。

    - provider=local            → ok（資訊性，與 v0.7 行為一致）
    - provider=remote 且可達     → ok（顯示 endpoint）
    - provider=remote 連不到     → fail + 三條 next-step（不靜默降級回本地）
    - config 無效（remote 缺 URL）→ fail
    """
    sys.path.insert(0, str(SYSTEM_DIR))
    try:
        import embed_backend as eb
    except ImportError as exc:
        return _check(None, "embedding backend", f"embed_backend import failed: {exc}")

    try:
        cfg = eb.resolve_config()
    except eb.EmbeddingBackendError as exc:
        return _check(
            False,
            "embedding backend",
            str(exc),
            "Fix MEMOSYNE_EMBED_* in .env, or unset MEMOSYNE_EMBED_PROVIDER to use local.",
        )

    if not cfg.is_remote:
        return _check(True, "embedding backend", cfg.describe())

    ok, detail = eb.ping_endpoint(cfg, timeout=3.0)
    if ok:
        return _check(True, "embedding backend", f"{cfg.provider} {cfg.model} @ {detail}")
    return _check(
        False,
        "embedding backend",
        detail,
        "1. tailscale status            (確認 mesh 健康)\n"
        "       2. ssh <host> && systemctl status ollama   (確認遠端服務在跑)\n"
        "       3. or unset MEMOSYNE_EMBED_PROVIDER to use the local fallback",
    )


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

    expected_keys = {
        "chronicle_jsonl",
        "chronicle_db",
        "bm25_index",
        "contextual_cache",
        "hyqe_cache",
        "tapestry_db",
        "muse_centroids",
        "chroma_db",
    }
    for artifact in artifact_manifest():
        exists = bool(artifact["exists"])
        is_expected = artifact["key"] in expected_keys
        is_ephemeral = bool(artifact.get("ephemeral"))

        if is_expected:
            # Must exist; absent → fail (red)
            status: bool | None = exists
        elif is_ephemeral:
            # Ephemeral marker / opt-in log: absent IS the normal state.
            # Only flag as info-ok regardless of existence; never warn.
            status = True
        else:
            # Unknown artifact: best-effort warn if absent (catches regressions)
            status = exists if exists else None

        checks.append(_check(
            status,
            artifact["key"],
            artifact["path"],
            "" if is_ephemeral else
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
        "Start Ollama with `ollama serve &` then `ollama pull <model>`,\n"
        "       OR pick a cloud backend instead: `memosyne providers list`.",
    ))

    checks.append(_embedding_backend_check())

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


def cmd_quickstart(_: argparse.Namespace) -> int:
    """
    v0.7 — The Threshold Ritual

    First-run experience: prove the whole pipeline works against the
    public sample vault, in under 5 minutes, without the user needing
    to read any docs first.

    Flow:
      1. Confirm a usable LLM provider exists (offer choices if not)
      2. Run eval --sample (rebuilds sample indexes + runs golden eval)
      3. Run two example searches and pretty-print the top results
      4. Print 'next steps' that point at the real-vault workflow
    """
    print("🜍 Memosyne — The Threshold Ritual")
    print()

    # Step 1: provider check
    sys.path.insert(0, str(SYSTEM_DIR))
    try:
        import providers as _providers
    except ImportError as e:
        print(f"[fail] providers module unavailable: {e}")
        print("       Run `pip install -e .` from the repo root and retry.")
        return 1

    print("Step 1 / 4 — Detect LLM provider")
    ready = []
    for p in _providers.PROVIDERS:
        status, detail = _providers._STATUS_FN[p.name]()
        marker = "✓" if status == "ok" else "—" if status == "no-key" else "✗"
        print(f"   {marker} {p.name:12} {status:12} {detail[:60]}")
        if status == "ok":
            ready.append(p.name)

    if not ready:
        print()
        print("No LLM provider is ready. Pick one and configure it:")
        print()
        print("   A. Local (private, free): brew install ollama && ollama pull gemma3:4b")
        print("   B. DeepSeek (cheap cloud): export DEEPSEEK_API_KEY=sk-... in .env")
        print("   C. OpenRouter (multi):    drop key into ./openrouter-key")
        print()
        print("Then rerun: memosyne quickstart")
        return 1

    print(f"   → {ready[0]} is ready; quickstart will use it.")
    print()

    # Step 2: sample eval (proves indexing + retrieval round-trip)
    print("Step 2 / 4 — Build sample-vault indexes and evaluate")
    print("   (this rebuilds Chroma + BM25 + Tapestry against sample_vault/_eval/golden.yaml)")
    ns = argparse.Namespace(
        sample=True, golden="", top_k=10, config="quickstart",
    )
    rc = cmd_eval(ns)
    if rc != 0:
        print()
        print("   eval failed; see output above. quickstart aborting.")
        return rc
    print()

    # Step 3: example searches
    print("Step 3 / 4 — Two example searches against sample_vault")
    sample_vault_dir = ROOT / "sample_vault"
    env_overrides = {
        "MEMOSYNE_VAULT_DIR":    str(sample_vault_dir),
        "MEMOSYNE_ARTIFACT_DIR": str(sample_vault_dir / "_artifacts"),
    }
    for q in ["watercolor", "Tokyo trip"]:
        print(f"\n   $ memosyne search {q!r} --top 3 --no-record-access")
        _run_script(
            "vectorize.py",
            ["--query", q, "--top", "3", "--no-record-access"],
            env_overrides=env_overrides,
        )

    # Step 4: next steps
    print()
    print("Step 4 / 4 — Next")
    print("   Add your own memory:")
    print("     cp your_journal.md spring/")
    print("     memosyne ingest")
    print()
    print("   Search your real vault (after first ingest):")
    print("     memosyne search '<question>' --walk deep")
    print()
    print("   Let the Muses interview you (daily ritual):")
    print("     memosyne call")
    print()
    print("   Periodic maintenance:")
    print("     memosyne slumber --reflect --days 14")
    print()
    print("   See `memosyne --help` for the full command surface.")
    print()
    print("🌊 The Spring of Memosyne is open. Begin the offering.")
    return 0


def cmd_rebuild(ns: argparse.Namespace) -> int:
    """
    v0.7: rebuild defaults to incremental. Pass --full to wipe everything.

    Incremental path:
      - vectorize.py with no flags
      - picks up new chunks (id-not-in-existing filter)
      - consumes dirty_paths.txt (v0.6 turn-aware update marker)
      - rebuilds BM25 from current chunks

    Full path (--full):
      - vectorize.py --rebuild
      - drops the Chroma collection and re-embeds every chunk
      - slow; use only when schema changed or index is corrupt
    """
    args = ["--rebuild"] if ns.full else []
    return _run_script("vectorize.py", args)


def cmd_search(ns: argparse.Namespace) -> int:
    args = ["--query", ns.query, "--top", str(ns.top)]
    if ns.type:
        args.extend(["--type", ns.type])
    if ns.no_record_access:
        args.append("--no-record-access")
    if ns.walk and ns.walk != "deep":
        args.extend(["--walk", ns.walk])
    if ns.return_parent:
        args.append("--return-parent")
    return _run_script("vectorize.py", args)


def cmd_eval(ns: argparse.Namespace) -> int:
    sample_vault_dir = ROOT / "sample_vault"
    sample_artifact_dir = sample_vault_dir / "_artifacts"
    sample_golden = sample_vault_dir / "_eval" / "golden.yaml"

    if ns.sample:
        if ns.golden:
            print("[fail] --sample and --golden are mutually exclusive")
            return 1
        if not sample_golden.exists():
            print(f"[fail] sample golden set not found: {sample_golden}")
            return 1
        if not sample_artifact_dir.exists():
            print(
                f"[fail] sample artifacts missing at {sample_artifact_dir}\n"
                "       run rebuild against sample_vault first:\n"
                f"       MEMOSYNE_VAULT_DIR={sample_vault_dir} "
                f"MEMOSYNE_ARTIFACT_DIR={sample_artifact_dir} "
                "python memosyne.py rebuild"
            )
            return 1
        env_overrides = {
            "MEMOSYNE_VAULT_DIR": str(sample_vault_dir),
            "MEMOSYNE_ARTIFACT_DIR": str(sample_artifact_dir),
        }
        golden_path = sample_golden
    else:
        if not ns.golden:
            print("[fail] either --sample or --golden <path> is required")
            return 1
        env_overrides = {}
        golden_path = Path(ns.golden).expanduser().resolve()

    args = [
        "--golden", str(golden_path),
        "--top-k", str(ns.top_k),
        "--config", ns.config,
    ]
    script = _system_script("eval_golden.py")
    cmd = [PYTHON, str(script), *args]
    env = {**os.environ, **env_overrides}
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


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
    extra: list[str] = []
    if ns.http:
        extra.append("--http")
        extra.extend(["--host", ns.host])
        extra.extend(["--port", str(ns.port)])
    return _run_script("mcp_server.py", extra)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memosyne",
        description="Memosyne — daily command surface for the personal memory vault",
    )
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="ensure core directories exist")
    init.set_defaults(func=cmd_init)

    quickstart = subparsers.add_parser(
        "quickstart",
        help="first-run experience: detect provider, build sample indexes, run two searches",
    )
    quickstart.set_defaults(func=cmd_quickstart)

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
    search.add_argument(
        "--walk",
        choices=["deep", "fast", "off"],
        default="deep",
        help="graph walk strategy: deep=PPR spreading (default), "
             "fast=two-pass walk, off=skip graph contribution",
    )
    search.add_argument(
        "--return-parent",
        action="store_true",
        help="replace snippet with the full parent H2 section "
             "(small-to-big retrieval)",
    )
    search.set_defaults(func=cmd_search)

    _add_passthrough(subparsers, "ingest", "run The Spring Ritual", "ingest.py")

    _add_passthrough(
        subparsers, "call",
        "The Call of the Muses — daily proactive questions that fill memory gaps",
        "muse_call.py",
    )

    # `rebuild` used to always pass --rebuild (full wipe-and-rebuild). v0.7
    # changes the default to incremental — for daily ingest workflow that
    # only needs to embed a few new chunks, full rebuild is grossly wasteful
    # (21K chunks re-embedded for ~30 new ones). Pass --full to force.
    rebuild = subparsers.add_parser(
        "rebuild",
        help="rebuild retrieval indexes (default: incremental; use --full to wipe and rebuild)",
    )
    rebuild.add_argument(
        "--full",
        action="store_true",
        help="wipe Chroma + BM25 and rebuild from scratch (slow; only when schema "
             "changed or index is suspected corrupt)",
    )
    rebuild.set_defaults(func=cmd_rebuild)

    _add_passthrough(subparsers, "enrich",
                     "run The Weaving (LLM entity + theme enrichment)",
                     "enrich.py")
    _add_passthrough(subparsers, "contextualize",
                     "The Illumination — generate contextual paragraph summaries",
                     "vectorize.py", ["--contextualize"])
    _add_passthrough(subparsers, "hyqe",
                     "The Triple Echo — generate hypothetical questions per chunk",
                     "vectorize.py", ["--hyqe"])
    _add_passthrough(subparsers, "slumber", "run The Rite of Slumber", "slumber.py")
    _add_passthrough(subparsers, "chronicle", "inspect The Chronicle of Mneme", "mneme_weight.py")
    _add_passthrough(subparsers, "tapestry", "inspect or rebuild The Tapestry", "tapestry.py")
    _add_passthrough(subparsers, "correct", "run Aletheia correction tools", "aletheia.py")
    _add_passthrough(
        subparsers, "query-log",
        "The Augury Replay — capture stats / export / replay (opt-in)",
        "query_log.py",
    )

    eval_p = subparsers.add_parser(
        "eval",
        help="run a golden-set retrieval evaluation",
    )
    eval_p.add_argument(
        "--sample",
        action="store_true",
        help="evaluate against sample_vault/_eval/golden.yaml with auto-set env",
    )
    eval_p.add_argument(
        "--golden",
        default="",
        help="path to a golden_set.yaml (omit when --sample is used)",
    )
    eval_p.add_argument("--top-k", type=int, default=10)
    eval_p.add_argument("--config", default="baseline")
    eval_p.set_defaults(func=cmd_eval)

    mcp = subparsers.add_parser("mcp", help="run or check the MCP server")
    mcp.add_argument("--check", action="store_true", help="import-check the MCP server without starting it")
    mcp.add_argument("--print-config", action="store_true", help="print an MCP client config snippet")
    mcp.add_argument("--name", default="memosyne", help="MCP server name for --print-config")
    mcp.add_argument("--http", action="store_true",
                     help="serve over streamable HTTP (bearer-token auth required)")
    mcp.add_argument("--host", default="127.0.0.1",
                     help="HTTP bind host (default 127.0.0.1)")
    mcp.add_argument("--port", type=int, default=8000,
                     help="HTTP bind port (default 8000)")
    mcp.set_defaults(func=cmd_mcp)

    _add_passthrough(
        subparsers, "auth",
        "manage HTTP bearer tokens (create / list / revoke)",
        "auth.py",
    )

    _add_passthrough(
        subparsers, "providers",
        "inspect LLM provider status and test connectivity (list / test)",
        "providers.py",
    )

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
