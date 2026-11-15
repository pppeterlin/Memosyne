"""
Memosyne command surface.

One predictable entry point. Most subcommands hand their arguments straight
to the module that owns the feature (``python -m memosyne.<module>``), so each
module stays runnable and testable on its own.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from memosyne import paths

PYTHON = sys.executable

# Domain folders created by `memosyne init`.
DOMAINS = ["10_Profile", "20_AI_Chats", "30_Journal", "40_Projects", "50_Knowledge"]

# subcommand → (help, module, fixed args). Arguments after the subcommand are
# passed through untouched.
PASSTHROUGH: dict[str, tuple[str, str, list[str]]] = {
    # core
    "ingest": ("run The Spring Ritual: route, enrich and index files in spring/",
               "memosyne.ingest", []),
    "enrich": ("run The Weaving (LLM entity + theme enrichment)",
               "memosyne.enrich", []),
    "digest": ("rebuild the deterministic digests (timeline + entity index)",
               "memosyne.digest", []),
    "call": ("The Call of the Muses — daily questions that fill memory gaps",
             "memosyne.gap_questions", []),
    "chronicle": ("inspect The Chronicle of Mneme (access log)",
                  "memosyne.access_log", []),
    "auth": ("manage HTTP bearer tokens (create / list / revoke)",
             "memosyne.auth", []),
    "providers": ("inspect LLM / embedding provider status (list / test)",
                  "memosyne.providers", []),
    # labs
    "contextualize": ("[labs] The Illumination — contextual paragraph summaries",
                      "memosyne.retrieval", ["--contextualize"]),
    "hyqe": ("[labs] The Triple Echo — hypothetical questions per chunk",
             "memosyne.retrieval", ["--hyqe"]),
    "slumber": ("[labs] The Rite of Slumber — memory consolidation",
                "memosyne.labs.consolidate", []),
    "tapestry": ("[labs] inspect or rebuild The Tapestry (entity graph)",
                 "memosyne.labs.graph", []),
    "correct": ("[labs] Aletheia — audited corrections",
                "memosyne.labs.corrections", []),
    "audit": ("[labs] The Augury — memory quality audit",
              "memosyne.labs.audit", []),
    "query-log": ("[labs] capture stats / export / replay of real queries",
                  "memosyne.labs.query_log", []),
    "chat": ("[labs] RAG chat REPL over the vault",
             "memosyne.labs.chat", []),
}


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


def _run_module(module: str, args: list[str],
                env_overrides: dict[str, str] | None = None) -> int:
    cmd = [PYTHON, "-m", module, *args]
    env = {**os.environ, **env_overrides} if env_overrides else None
    sys.stdout.flush()   # keep our own output ahead of the child's when piped
    return subprocess.call(cmd, env=env)


def _status_line(check: HealthCheck) -> str:
    suffix = f" {check.detail}" if check.detail else ""
    hint = f"\n       next: {check.hint}" if check.hint and check.status != "ok" else ""
    return f"[{check.status}] {check.label}{suffix}{hint}"


def _check(ok: bool | None, label: str, detail: str = "", hint: str = "") -> HealthCheck:
    tag = "ok" if ok is True else "warn" if ok is None else "fail"
    return HealthCheck(tag, label, detail, hint)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def search_extra_installed() -> bool:
    return _module_available("chromadb") and _module_available("sentence_transformers")


def _embedding_backend_check() -> HealthCheck:
    """Report the embedding backend; a configured remote endpoint must be reachable."""
    try:
        from memosyne import embedding as eb
        cfg = eb.resolve_config()
    except Exception as exc:  # EmbeddingBackendError or import problems
        return _check(
            False, "embedding backend", str(exc),
            "Fix MEMOSYNE_EMBED_* in .env, or unset MEMOSYNE_EMBED_PROVIDER to use local.",
        )

    if not cfg.is_remote:
        return _check(True, "embedding backend", cfg.describe())

    ok, detail = eb.ping_endpoint(cfg, timeout=3.0)
    if ok:
        return _check(True, "embedding backend", f"{cfg.provider} {cfg.model} @ {detail}")
    return _check(
        False, "embedding backend", detail,
        "1. check the network path to the endpoint (VPN / mesh)\n"
        "       2. confirm the remote embedding service is running\n"
        "       3. or unset MEMOSYNE_EMBED_PROVIDER to use the local fallback",
    )


def _llm_provider_check() -> HealthCheck:
    try:
        from memosyne import providers
        ready = [p.name for p in providers.PROVIDERS
                 if providers._STATUS_FN[p.name]()[0] == "ok"]
    except Exception as exc:
        return _check(None, "LLM provider", f"status unavailable: {exc}")
    if ready:
        return _check(True, "LLM provider", "ready: " + ", ".join(ready))
    return _check(
        None, "LLM provider", "none ready (only needed for enrichment)",
        "Run `memosyne providers list`, or ingest with --no-enrich.",
    )


def _check_secret_files() -> HealthCheck:
    # `.env` is the documented place for keys (git-ignored); loose key files are not.
    names = ["openrouter-key", "openrouter.py", "ANTHROPIC_API_KEY"]
    present = [n for n in names if (paths.home() / n).exists()]
    if not present:
        return _check(True, "secret hygiene", "no loose key files in the project directory")
    return _check(
        None, "secret hygiene",
        "loose key files in the project directory: " + ", ".join(present),
        "Prefer `.env`; make sure these files are git-ignored and never published.",
    )


def _collect_health_checks() -> list[HealthCheck]:
    checks: list[HealthCheck] = []
    vault = paths.data_root()
    spring = paths.spring_dir()
    memories = sum(1 for _ in paths.iter_memory_files(vault))
    has_search = search_extra_installed()
    full_profile = os.getenv("MEMOSYNE_SEARCH_PROFILE", "core").strip().lower() == "full"

    checks.append(_check(
        sys.version_info >= (3, 10),
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        f"executable={sys.executable}; requires 3.10+",
    ))
    checks.append(_check(True, "home", str(paths.home())))
    checks.append(_check(
        spring.exists() or None, "The Spring", str(spring), "Run: memosyne init"))
    checks.append(_check(
        vault.exists() or None, "The Vault", f"{vault} ({memories} memories)",
        "Run: memosyne init, then drop files into spring/ and run: memosyne ingest"))

    for module, extra in [("yaml", "core"), ("mcp", "core"), ("rank_bm25", "core")]:
        checks.append(_check(
            _module_available(module), f"import {module}", "",
            f"Reinstall the {extra} dependencies: pip install -e ."))
    checks.append(_check(
        True, "search extra",
        "installed (dense + keyword retrieval)" if has_search
        else 'not installed — keyword search only; add with: pip install -e ".[search]"'))

    active_layers = {"core"} | ({"search"} if has_search else set()) \
        | ({"labs"} if full_profile else set())
    for artifact in paths.artifact_manifest():
        exists = bool(artifact["exists"])
        if exists and artifact["expects_file"] and artifact["is_dir"]:
            # e.g. a docker-compose bind mount of a file that wasn't there yet.
            # Readers silently see an empty cache; writers crash. Fail loudly.
            checks.append(_check(
                False, artifact["key"],
                artifact["path"] + " (expected a file, found a directory)",
                "Remove the empty directory and let the tool recreate the file.",
            ))
            continue
        required = (artifact["layer"] in active_layers
                    and not artifact["ephemeral"] and memories > 0)
        if exists or not required:
            if exists or artifact["layer"] in active_layers:
                detail = artifact["path"] if exists else "(not created yet)"
                checks.append(_check(True, artifact["key"], detail))
            continue
        checks.append(_check(
            None, artifact["key"], artifact["path"] + " (missing)",
            "Run: memosyne rebuild"))

    checks.append(_llm_provider_check())
    if has_search:
        checks.append(_embedding_backend_check())
    checks.append(_check_secret_files())
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
    vault = paths.data_root()
    created = []
    for path in [paths.spring_dir() / "_processed", *(vault / d for d in DOMAINS)]:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    print(f"home   : {paths.home()}")
    print(f"spring : {paths.spring_dir()}")
    print(f"vault  : {vault}")
    print(f"{len(created)} folder(s) created." if created
          else "The Spring and the Vault are already present.")
    print("\nNext: copy a file into spring/ and run `memosyne ingest`.")
    return 0


def _sample_env() -> dict[str, str] | None:
    sample = paths.sample_vault_dir()
    if sample is None:
        return None
    return {
        "MEMOSYNE_VAULT_DIR": str(sample),
        "MEMOSYNE_ARTIFACT_DIR": str(sample / "_artifacts"),
    }


def cmd_quickstart(_: argparse.Namespace) -> int:
    """First-run experience: prove the pipeline works against the sample vault."""
    print("🜍 Memosyne — The Threshold Ritual\n")
    env = _sample_env()
    if env is None:
        print("[fail] the sample vault ships with the source checkout only.")
        print("       git clone the repository and run `pip install -e .` there.")
        return 1

    print("Step 1 / 4 — Build sample-vault indexes")
    mode = "dense + keyword" if search_extra_installed() else "keyword only"
    print(f"   retrieval mode: {mode}")
    rc = _run_module("memosyne.retrieval", ["--rebuild"], env_overrides=env)
    if rc != 0:
        print("\n   index build failed; see output above. quickstart aborting.")
        return rc

    print("\nStep 2 / 4 — Evaluate against the sample golden set")
    rc = cmd_eval(argparse.Namespace(sample=True, golden="", top_k=10, config="baseline"))
    if rc != 0:
        print("\n   eval failed; see output above. quickstart aborting.")
        return rc

    print("\nStep 3 / 4 — Two example searches against sample_vault")
    for q in ["watercolor", "Tokyo trip"]:
        print(f"\n   $ memosyne search {q!r} --top 3 --no-record-access")
        _run_module("memosyne.retrieval",
                    ["--query", q, "--top", "3", "--no-record-access"], env_overrides=env)

    print("\nStep 4 / 4 — The digest: the whole vault at a glance")
    print("\n   $ memosyne digest --print entities")
    _run_module("memosyne.digest", ["--print", "entities"], env_overrides=env)

    print("\nNext")
    print("   memosyne init                 # create spring/ and your vault")
    print("   cp your_journal.md spring/ && memosyne ingest")
    print("   memosyne search '<question>'")
    print("   memosyne digest               # timeline + entity index for agents")
    print("   memosyne call                 # let the Muses interview you")
    print("\n🌊 The Spring of Memosyne is open. Begin the offering.")
    return 0


def cmd_rebuild(ns: argparse.Namespace) -> int:
    """Incremental by default; --full wipes and re-embeds everything."""
    return _run_module("memosyne.retrieval", ["--rebuild"] if ns.full else [])


def cmd_search(ns: argparse.Namespace) -> int:
    args = ["--query", ns.query, "--top", str(ns.top)]
    if ns.type:
        args.extend(["--type", ns.type])
    if ns.no_record_access:
        args.append("--no-record-access")
    if ns.profile:
        args.extend(["--profile", ns.profile])
    if ns.include_chats:
        args.append("--include-chats")
    if ns.walk:
        args.extend(["--walk", ns.walk])
    if ns.return_parent:
        args.append("--return-parent")
    return _run_module("memosyne.retrieval", args)


def cmd_eval(ns: argparse.Namespace) -> int:
    env_overrides: dict[str, str] = {}
    if ns.sample:
        if ns.golden:
            print("[fail] --sample and --golden are mutually exclusive")
            return 1
        env = _sample_env()
        if env is None:
            print("[fail] the sample vault ships with the source checkout only")
            return 1
        sample = Path(env["MEMOSYNE_VAULT_DIR"])
        golden_path = sample / "_eval" / "golden.yaml"
        if not golden_path.exists():
            print(f"[fail] sample golden set not found: {golden_path}")
            return 1
        if not (sample / "_artifacts").exists():
            print(
                f"[fail] sample artifacts missing at {sample / '_artifacts'}\n"
                "       build them first: memosyne quickstart"
            )
            return 1
        env_overrides = env
    else:
        if not ns.golden:
            print("[fail] either --sample or --golden <path> is required")
            return 1
        golden_path = Path(ns.golden).expanduser().resolve()

    args = ["--golden", str(golden_path), "--top-k", str(ns.top_k), "--config", ns.config]
    return _run_module("memosyne.labs.golden_eval", args, env_overrides=env_overrides)


def cmd_mcp(ns: argparse.Namespace) -> int:
    if ns.print_config:
        config = {
            "mcpServers": {
                ns.name: {
                    "command": sys.executable,
                    "args": ["-m", "memosyne.mcp_server"],
                    "env": {"MEMOSYNE_HOME": str(paths.home())},
                }
            }
        }
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return 0
    if ns.check:
        try:
            import memosyne.mcp_server  # noqa: F401
        except Exception as exc:
            print(f"[fail] MCP server import failed: {exc}")
            return 1
        print("[ok] MCP server imports successfully")
        return 0
    extra: list[str] = []
    if ns.http:
        extra += ["--http", "--host", ns.host, "--port", str(ns.port)]
    return _run_module("memosyne.mcp_server", extra)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memosyne",
        description="Memosyne — local-first personal memory for AI agents",
    )
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="create spring/ and the vault folders")
    init.set_defaults(func=cmd_init)

    quickstart = subparsers.add_parser(
        "quickstart",
        help="first-run demo against the synthetic sample vault",
    )
    quickstart.set_defaults(func=cmd_quickstart)

    health = subparsers.add_parser("health", help="check runtime and artifact health")
    health.add_argument("--json", action="store_true", help="emit machine-readable results")
    health.set_defaults(func=cmd_health)

    search = subparsers.add_parser("search", help="search memories")
    search.add_argument("query")
    search.add_argument("--top", type=int, default=5)
    search.add_argument("--type", default="")
    search.add_argument("--no-record-access", action="store_true",
                        help="do not write this search to the access log")
    search.add_argument("--profile", choices=["core", "full"], default="",
                        help="core = dense + keyword fusion (default); "
                             "full = also graph, ACT-R and routing from memosyne.labs")
    search.add_argument("--include-chats", action="store_true",
                        help="also search AI chat transcripts (life memories only by default)")
    search.add_argument("--walk", choices=["deep", "fast", "off"], default="",
                        help="[full profile] graph walk strategy")
    search.add_argument("--return-parent", action="store_true",
                        help="replace the snippet with its full parent section")
    search.set_defaults(func=cmd_search)

    rebuild = subparsers.add_parser(
        "rebuild",
        help="update retrieval indexes (incremental; --full to wipe and rebuild)",
    )
    rebuild.add_argument("--full", action="store_true",
                         help="wipe and rebuild every index from scratch (slow)")
    rebuild.set_defaults(func=cmd_rebuild)

    for name, (help_text, module, fixed) in PASSTHROUGH.items():
        sub = subparsers.add_parser(name, help=help_text, add_help=False)
        sub.set_defaults(passthrough_module=module, passthrough_fixed=fixed)

    eval_p = subparsers.add_parser("eval", help="run a golden-set retrieval evaluation")
    eval_p.add_argument("--sample", action="store_true",
                        help="evaluate against sample_vault/_eval/golden.yaml")
    eval_p.add_argument("--golden", default="",
                        help="path to a golden_set.yaml (omit when --sample is used)")
    eval_p.add_argument("--top-k", type=int, default=10)
    eval_p.add_argument("--config", default="baseline")
    eval_p.set_defaults(func=cmd_eval)

    mcp = subparsers.add_parser("mcp", help="run or check the MCP server")
    mcp.add_argument("--check", action="store_true",
                     help="import-check the MCP server without starting it")
    mcp.add_argument("--print-config", action="store_true",
                     help="print an MCP client config snippet")
    mcp.add_argument("--name", default="memosyne", help="server name for --print-config")
    mcp.add_argument("--http", action="store_true",
                     help="serve over streamable HTTP (bearer-token auth required)")
    mcp.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    mcp.add_argument("--port", type=int, default=8000, help="HTTP bind port")
    mcp.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    paths.load_env()
    parser = build_parser()
    ns, extra = parser.parse_known_args(argv)
    if hasattr(ns, "passthrough_module"):
        return _run_module(ns.passthrough_module, [*ns.passthrough_fixed, *extra])
    if not hasattr(ns, "func"):
        parser.print_help()
        return 0
    if extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return int(ns.func(ns))


if __name__ == "__main__":
    raise SystemExit(main())
