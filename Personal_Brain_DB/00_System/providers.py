#!/usr/bin/env python3
"""
Memosyne — provider inspector + tester (v0.7)

`memosyne providers list` and `memosyne providers test <name>` give a
single-screen view of which LLM backends are configured and which
actually work right now. Borrowed in spirit from gbrain's
`providers list` but scoped to Memosyne's four backends:

    ollama       local        Ollama HTTP API
    openrouter   cloud        OpenRouter routing
    deepseek     cloud        DeepSeek official API
    proxy        local/relay  OpenAI-compatible reverse proxy

The goal is: a new user picks ONE provider, gets it green here, and
the rest of Memosyne just works. Today they have to read llm_client.py
to figure out env vars + default models.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Load .env so the same env vars Memosyne uses at runtime show here too
try:
    from dotenv import load_dotenv as _load_dotenv
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    _ENV_FILE = _REPO_ROOT / ".env"
    if _ENV_FILE.exists():
        _load_dotenv(_ENV_FILE)
except ImportError:
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ── Provider catalog ────────────────────────────────────────

@dataclass
class Provider:
    name: str
    kind: str         # local | cloud | local/relay
    default_model: str
    env_hint: str     # what env var(s) configure this
    notes: str        # one-line guidance


PROVIDERS: list[Provider] = [
    Provider(
        name="ollama",
        kind="local",
        default_model="gemma4:26b",
        env_hint="OLLAMA_HOST (default http://127.0.0.1:11434)",
        notes="Start with `ollama serve &` then `ollama pull <model>`. Zero cost, zero data leaves machine.",
    ),
    Provider(
        name="openrouter",
        kind="cloud",
        default_model="google/gemma-3-27b-it",
        env_hint="OPENROUTER_API_KEY (or openrouter-key file at repo root)",
        notes="Routes to many providers. Use 'openrouter:auto' for fallback chain.",
    ),
    Provider(
        name="deepseek",
        kind="cloud",
        default_model="deepseek-v4-pro",
        env_hint="DEEPSEEK_API_KEY (or LLM_API_KEY in .env)",
        notes="Frontier reasoning, cheap. v0.6+ supports reasoning_effort + thinking.",
    ),
    Provider(
        name="proxy",
        kind="local/relay",
        default_model="claude-opus-4-6",
        env_hint="PROXY_BASE_URL + PROXY_API_KEY (or ANTHROPIC_API_KEY file)",
        notes="For OpenAI-compatible reverse proxies (aiclient-2-api, LiteLLM, etc.).",
    ),
]


# ── Status detection ────────────────────────────────────────

def _ollama_status() -> tuple[str, str]:
    """('ok' | 'unreachable' | 'no-models', detail)"""
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    url = f"{host}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            data = json.loads(r.read().decode())
            models = [m.get("name", "?") for m in data.get("models", [])]
            if not models:
                return "no-models", "API reachable but no models installed"
            return "ok", f"{len(models)} models: {', '.join(models[:3])}{'...' if len(models) > 3 else ''}"
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        return "unreachable", f"{type(e).__name__}: {e}"
    except Exception as e:
        return "unreachable", f"{type(e).__name__}: {e}"


def _key_present(env_names: list[str], file_paths: list[Path]) -> tuple[bool, str]:
    for env in env_names:
        v = os.environ.get(env, "").strip()
        if v:
            return True, f"env {env} ({len(v)} chars)"
    for f in file_paths:
        if f.exists():
            content = f.read_text(encoding="utf-8", errors="ignore").strip()
            if content:
                return True, f"file {f.name} ({len(content)} chars)"
    return False, "no key found"


def _openrouter_status() -> tuple[str, str]:
    ok, detail = _key_present(
        ["OPENROUTER_API_KEY", "OPENROUTER_KEY"],
        [_REPO_ROOT / "openrouter-key"],
    )
    return ("ok" if ok else "no-key"), detail


def _deepseek_status() -> tuple[str, str]:
    ok, detail = _key_present(
        ["DEEPSEEK_API_KEY", "LLM_API_KEY"],
        [],
    )
    return ("ok" if ok else "no-key"), detail


def _proxy_status() -> tuple[str, str]:
    ok, detail = _key_present(
        ["PROXY_API_KEY", "ANTHROPIC_API_KEY"],
        [_REPO_ROOT / "ANTHROPIC_API_KEY"],
    )
    if not ok:
        return "no-key", detail
    base = os.environ.get("PROXY_BASE_URL", "")
    if not base:
        return "ok", f"key {detail} (PROXY_BASE_URL unset — using default)"
    return "ok", f"key {detail}; base_url={base}"


_STATUS_FN = {
    "ollama":     _ollama_status,
    "openrouter": _openrouter_status,
    "deepseek":   _deepseek_status,
    "proxy":      _proxy_status,
}


# ── Commands ────────────────────────────────────────────────

def _color(status: str) -> str:
    return {
        "ok":          "\033[32m✓\033[0m",
        "no-key":      "\033[33m—\033[0m",
        "no-models":   "\033[33m!\033[0m",
        "unreachable": "\033[31m✗\033[0m",
    }.get(status, status)


def cmd_list() -> int:
    print(f"{'provider':12} {'kind':12} {'status':5} {'detail'}")
    print("-" * 78)
    for p in PROVIDERS:
        status, detail = _STATUS_FN[p.name]()
        print(f"{p.name:12} {p.kind:12} {_color(status):>5} {detail}")
    print()
    print("Set the active backend with LLM_PROVIDER=<name> in .env,")
    print("or per-call via 'memosyne <cmd> --model <name>:<model>'.")
    return 0


def cmd_test(name: str, model: str | None = None) -> int:
    """Run a single hello-world LLM call against the named provider."""
    if name not in _STATUS_FN:
        print(f"unknown provider: {name}", file=sys.stderr)
        print(f"available: {', '.join(p.name for p in PROVIDERS)}", file=sys.stderr)
        return 2

    status, detail = _STATUS_FN[name]()
    if status != "ok":
        print(f"[{name}] not ready: {status} — {detail}", file=sys.stderr)
        print(f"see: memosyne providers list", file=sys.stderr)
        return 1

    pinfo = next(p for p in PROVIDERS if p.name == name)
    model_name = model or pinfo.default_model
    fq_model = f"{name}:{model_name}" if name != "ollama" else model_name

    print(f"[{name}] testing with model: {fq_model}")
    print(f"        prompt: 'Reply with the single word: OK'")

    # Lazy import so test doesn't fail when llm_client has issues
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from llm_client import chat_text
    except ImportError as e:
        print(f"        ✗ llm_client unavailable: {e}", file=sys.stderr)
        return 1

    t0 = time.time()
    try:
        reply = chat_text(
            model=fq_model,
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
            temperature=0,
        )
    except Exception as e:
        print(f"        ✗ call failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    latency_ms = int((time.time() - t0) * 1000)

    print(f"        ✓ reply ({latency_ms}ms): {reply[:120]!r}")
    return 0


# ── CLI ─────────────────────────────────────────────────────

def _main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Memosyne LLM provider inspector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show every provider with status")
    ap_t = sub.add_parser("test", help="run a hello-world call against a provider")
    ap_t.add_argument("name", help="provider name (ollama / openrouter / deepseek / proxy)")
    ap_t.add_argument("--model", default="", help="override model name (default = provider's default)")
    args = ap.parse_args()

    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "test":
        return cmd_test(args.name, model=args.model or None)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
