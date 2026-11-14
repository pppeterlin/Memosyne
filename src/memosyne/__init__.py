"""Memosyne — local-first personal memory for AI agents.

Layers (see docs/architecture.md):

  core    ingest → enrich → digest → keyword search → MCP        (always installed)
  search  dense vector retrieval                                  (pip install "memosyne[search]")
  labs    graph, ACT-R reranking, routing, consolidation, audit   (memosyne.labs, opt-in)
"""

__version__ = "1.5.0"
