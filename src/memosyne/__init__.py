"""Memosyne — local-first personal memory for AI agents.

Layers (see docs/architecture.md):

  core    ingest → enrich → digest → keyword search → MCP        (always installed)
  search  dense vector retrieval                                  (pip install "memosyne[search]")
  labs    graph, ACT-R reranking, routing, consolidation, audit   (memosyne.labs, opt-in)
"""

from memosyne.paths import load_env as _load_env

# Settings in <home>/.env must be visible before any module resolves a path
# or reads a feature switch at import time — including when a module is run
# directly (`python -m memosyne.mcp_server`) rather than through the CLI.
_load_env()

__version__ = "1.5.0"
