# Acknowledgements & References

Memosyne stands on the shoulders of excellent research and open-source work. The techniques below are cited in the architecture and per-release planning docs under [docs/](.) (earlier design memos kept in [docs/archive/](docs/archive/)). Respect where it's due.

## Foundational techniques already implemented

- **ACT-R cognitive decay** — Anderson, J. R., et al. *An integrated theory of the mind.* Psychological Review (2004). The base-level activation formula `B_i = ln(Σ t_k^{-d})` powers The Chronicle of Mneme.
- **Contextual Retrieval** — Anthropic (2024). [Introducing Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval). Inspired The Illumination.
- **Reciprocal Rank Fusion (RRF)** — Cormack, G. V., Clarke, C. L. A., & Büttcher, S. *Reciprocal rank fusion outperforms Condorcet and individual rank learning methods.* SIGIR (2009).
- **BM25** — Robertson, S., & Zaragoza, H. *The Probabilistic Relevance Framework: BM25 and Beyond.* Foundations and Trends in IR (2009).
- **Personalized PageRank** — Haveliwala, T. H. *Topic-sensitive PageRank.* WWW (2002). Powers Tapestry spreading-activation retrieval.
- **HippoRAG** — Gutiérrez, B. J., et al. [*HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs.*](https://arxiv.org/abs/2405.14831) NeurIPS (2024).

## Research informing Retrieval v2

- **HippoRAG 2** — Gutiérrez, B. J., et al. [arxiv 2502.14802](https://arxiv.org/abs/2502.14802) (2025). Phrase+passage unified graph PPR.
- **Zep / Graphiti** — Rasmussen, P., et al. [*Zep: A Temporal Knowledge Graph Architecture for Agent Memory.*](https://arxiv.org/abs/2501.13956) (2025). Bi-temporal edges & edge invalidation. OSS: [getzep/graphiti](https://github.com/getzep/graphiti).
- **Mem0** — Chhikara, P., et al. [*Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory.*](https://arxiv.org/abs/2504.19413) (2025). CRUD memory ops + contradiction resolution.
- **A-MEM** — Xu, W., et al. [*A-MEM: Agentic Memory for LLM Agents.*](https://arxiv.org/abs/2502.12110) (2025). Zettelkasten-style memory evolution.
- **LightRAG** — Guo, Z., et al. [*LightRAG: Simple and Fast Retrieval-Augmented Generation.*](https://arxiv.org/abs/2410.05779) (2024). Incremental dual-level graph updates.
- **GraphRAG** — Edge, D., et al. [*From Local to Global: A Graph RAG Approach to Query-Focused Summarization.*](https://arxiv.org/abs/2404.16130) Microsoft Research (2024).
- **HyDE** — Gao, L., et al. [*Precise Zero-Shot Dense Retrieval without Relevance Labels.*](https://arxiv.org/abs/2212.10496) ACL (2023). Basis for The Triple Echo's HyQE view.
- **Self-RAG** — Asai, A., et al. [*Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection.*](https://arxiv.org/abs/2310.11511) ICLR (2024). Basis for The Mirror of Truth.
- **ColBERT / ColBERTv2** — Khattab, O., & Zaharia, M. [*ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT.*](https://arxiv.org/abs/2004.12832) SIGIR (2020).
- **MemGPT / Letta** — Packer, C., et al. [*MemGPT: Towards LLMs as Operating Systems.*](https://arxiv.org/abs/2310.08560) (2023).
- **LongMemEval** — Wu, D., et al. [*LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory.*](https://arxiv.org/abs/2410.10813) (2024). Methodology informing The Augury Benchmark.
- **RAGAS** — Es, S., et al. [*RAGAS: Automated Evaluation of Retrieval Augmented Generation.*](https://docs.ragas.io) EACL (2024).

## Tools & libraries

- [ChromaDB](https://www.trychroma.com/) — vector store
- [Kuzu](https://kuzudb.com/) — embedded graph database
- [NetworkX](https://networkx.org/) — in-memory graph algorithms & PageRank
- [rank-bm25](https://github.com/dorianbrown/rank_bm25) — BM25 implementation
- [sentence-transformers](https://www.sbert.net/) — multilingual embedding (`paraphrase-multilingual-MiniLM-L12-v2`)
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [Ollama](https://ollama.com/) — local LLM runtime

If a technique informs Memosyne and is not credited here, please open an issue — citations are load-bearing, not decorative.

---
