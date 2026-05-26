"""
End-to-end smoke test for the Memosyne MCP server, run against sample_vault.

This test is fully offline: it spawns Personal_Brain_DB/00_System/mcp_server.py
as a subprocess, completes the MCP initialize handshake, calls a few tools,
and asserts on the responses. No LLM is required.

Prerequisites
-------------
1. Python deps installed:    pip install -e .
                             pip install -r Personal_Brain_DB/00_System/requirements.txt
2. Sample indexes built:     MEMOSYNE_VAULT_DIR=$PWD/sample_vault \
                             MEMOSYNE_ARTIFACT_DIR=$PWD/sample_vault/_artifacts \
                             python memosyne.py rebuild

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
SERVER = REPO / "Personal_Brain_DB" / "00_System" / "mcp_server.py"
SAMPLE_VAULT = REPO / "sample_vault"
SAMPLE_ARTIFACTS = SAMPLE_VAULT / "_artifacts"

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


EXPECTED_TOOLS = {
    "search_memory",
    "get_profile",
    "list_journals",
    "read_file",
    "get_memory_health",
}


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
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
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
            print(f"[ok] tools/list returned {len(names)} tools")

            result = await session.call_tool(
                "search_memory",
                {"query": "Aiko", "top_k": 3},
            )
            text = "".join(b.text for b in result.content if hasattr(b, "text"))
            assert "Aiko" in text, "expected 'Aiko' in search_memory output"
            assert "sample_chat" in text or "20_AI_Chats" in text, (
                "expected the synthetic AI chat to be among top results"
            )
            print(f"[ok] search_memory returned {len(text)} chars containing 'Aiko'")

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
