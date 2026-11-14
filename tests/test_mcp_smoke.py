"""
End-to-end smoke test for the Memosyne MCP server, run against sample_vault.

Fully offline: spawns `python -m memosyne.mcp_server` as a subprocess,
completes the MCP initialize handshake, calls the core tools and asserts on
the responses. No LLM is required.

Prerequisites
-------------
    pip install -e .          # add ".[search]" to exercise dense retrieval too
    memosyne quickstart       # builds the sample indexes (or: make sample-rebuild)

Run
---
    python tests/test_mcp_smoke.py            # exits 0 on success, 1 on failure
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAMPLE_VAULT = REPO / "sample_vault"
SAMPLE_ARTIFACTS = SAMPLE_VAULT / "_artifacts"

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


EXPECTED_TOOLS = {
    "search_memory",
    "get_profile",
    "get_digest",
    "list_journals",
    "read_file",
    "get_memory_health",
    "muse_call",
    "muse_answer",
    "memosyne_guide",
}
# memosyne.labs tools stay hidden unless MEMOSYNE_MCP_TOOLS=all
LABS_TOOLS = {"optimize_memory", "aletheia_revert", "get_entity_timeline"}


async def run_smoke() -> None:
    if not SAMPLE_ARTIFACTS.exists():
        raise SystemExit(
            f"Sample artifacts missing at {SAMPLE_ARTIFACTS}.\n"
            "Run rebuild first (see this file's docstring)."
        )

    env = {
        **os.environ,
        "MEMOSYNE_VAULT_DIR": str(SAMPLE_VAULT),
        "MEMOSYNE_ARTIFACT_DIR": str(SAMPLE_ARTIFACTS),
        "MEMOSYNE_HF_OFFLINE": "1",
        "MEMOSYNE_MCP_TOOLS": "core",
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memosyne.mcp_server"],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("[ok] initialized")

            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            missing = EXPECTED_TOOLS - names
            assert not missing, f"missing tools: {missing}"
            leaked = LABS_TOOLS & names
            assert not leaked, f"labs tools exposed by default: {leaked}"
            print(f"[ok] tools/list returned {len(names)} tools")

            result = await session.call_tool(
                "search_memory",
                {"query": "Aiko", "top_k": 3},
            )
            text = "".join(b.text for b in result.content if hasattr(b, "text"))
            assert "Aiko" in text, "expected 'Aiko' in search_memory output"
            assert "20_AI_Chats" not in text, (
                "AI chats must stay out of the default (life-memory) search scope"
            )
            print(f"[ok] search_memory returned {len(text)} chars containing 'Aiko'")

            result = await session.call_tool(
                "search_memory",
                {"query": "Aiko", "top_k": 5, "include_chats": True},
            )
            text = "".join(b.text for b in result.content if hasattr(b, "text"))
            assert "20_AI_Chats" in text, (
                "expected the synthetic AI chat when include_chats=True"
            )
            print("[ok] search_memory(include_chats=True) reaches the AI chat")

            digest = await session.call_tool("get_digest", {"kind": "timeline"})
            digest_text = "".join(
                getattr(c, "text", "") for c in digest.content
            )
            assert "# Timeline" in digest_text, "get_digest returned no timeline"
            print("[ok] get_digest returned the timeline")

            outside = await session.call_tool("read_file", {"path": "../pyproject.toml"})
            outside_text = "".join(getattr(c, "text", "") for c in outside.content)
            assert "[project]" not in outside_text, "read_file escaped the vault"
            print("[ok] read_file refuses paths outside the vault")

            health = await session.call_tool("get_memory_health", {})
            health_text = "".join(
                b.text for b in health.content if hasattr(b, "text")
            )
            assert health_text.strip(), "get_memory_health returned no text"
            print("[ok] get_memory_health responded")


def main() -> int:
    try:
        asyncio.run(run_smoke())
    except AssertionError as exc:
        print(f"[fail] {exc}")
        return 1
    except Exception as exc:
        print(f"[fail] {type(exc).__name__}: {exc}")
        return 1
    print("\nMCP smoke test passed against sample_vault.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
