# Changelog

All notable changes to Memosyne are recorded here. Format inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow the project's `vMAJOR.MINOR` release sequence.

## [Unreleased] — v0.5 scale & self-eval

### Added

- **The Self-Weaving Tapestry (WS1)** — deterministic `link_extractor`
  pulls Memory → Entity edges from frontmatter, markdown links, and
  body shorthand. LLM enrichment retained for Entity → Entity edges
  and narrative metadata. Measured: 1454 deterministic edges vs 1431
  LLM-built (101.6%); see `docs/v0.5_ws1_deterministic_coverage.md`.
- **The Augury Replay (WS2)** — opt-in query capture
  (`MEMOSYNE_CAPTURE_QUERIES=1`) with PII scrub at write time.
  `memosyne query-log --export --since 7d > baseline.jsonl`,
  `memosyne query-log --replay baseline.jsonl --top-k 10` reports
  mean jaccard@k, top1 stability, and latency delta. Comparison is
  path-level by design — chunk_id is internal accounting that v0.6
  will change.
- **The Refined Chronicle (WS3)** — per-prefix ACT-R decay
  (`MEMOSYNE_CHRONICLE_DECAY` map), backlink boost
  (`score × (1 + 0.05 × ln(1 + edge_count))`), and a two-pass walk
  alternative to PPR (`memosyne search --walk fast|deep|off`,
  `MEMOSYNE_WALK` env). Toggle methodology under
  `sample_vault/_eval/toggle_reports/`.
- **The Open Threshold (WS4)** — `memosyne mcp --http` serves over
  streamable HTTP with bearer-token auth. `memosyne auth
  {create,list,revoke}` manages tokens stored at
  `~/.memosyne/tokens.sqlite` (SHA-256 hashed). Tools are tagged with
  required scope (read | write | admin) and `local_only` admin tools
  (`aletheia_revert`) are stripped from the HTTP-exposed registry.
  stdio transport unchanged.
- **The Codex of Skills (WS5)** — five new fat skills under
  `Personal_Brain_DB/00_System/skills/`: `memosyne-ingest`,
  `memosyne-search`, `memosyne-correction`, `memosyne-slumber`,
  `memosyne-augury`. `RESOLVER.md` maps trigger phrases (EN + ZH) and
  CLI verbs to the right skill.
- **The Vigil of Invariants (WS6)** — `make verify` wraps the four
  invariant checks (no real paths / no private names / sample vault
  synthetic / unit tests). GitHub Actions runs the same target on
  every push and PR to `master`.
- v0.6 planning doc — `docs/v0.6_accumulating_sources.md` captures
  the turn-level dedup design for sources that grow over time
  (Gemini continuations, journal append, chat exports).

### Changed

- `vectorize.search()` gains `walk: Literal["deep","fast","off"]`
  kwarg; CLI passes through from `memosyne search --walk`.
- `mcp_server.py` re-organized: tool definitions unchanged, but
  scope tags and HTTP transport added; bottom-of-file CLI now
  routes between stdio and HTTP based on `--http`.

### Notes

Retrieval quality target: hold v0.2 baseline (Recall@1 0.196 / R@5
0.582 / R@10 0.870 / MRR 0.358) and surface any drift via the
Augury Replay. Toggle ablation on the 5-question sample golden set
shows all four configurations identical — expected at small N; real
effect-size measurement is via replay against private capture or a
larger golden set.

## [0.4.0] — 2026-05-13 — OSS release

- `pyproject.toml` declares runtime + dev dependencies; `uv.lock`
  pinned for reproducible install.
- `tests/Dockerfile` for hermetic clean-install verification.
- Synthetic `sample_vault/` + `memosyne eval --sample` for offline
  retrieval evaluation; `tests/test_mcp_smoke.py` for offline MCP
  stdio smoke test.
- `docs/privacy.md`, `docs/sample_vault.md`, `docs/development.md`.
- Public docs decoupled from Ollama-specific assumptions.
- `MEMOSYNE_VAULT_DIR` now honored for data scanning, not just
  artifacts.

## [0.3.0] — 2026-05-08 — Productization

- Unified `memosyne` CLI surface: `init`, `ingest`, `search`,
  `rebuild`, `health`, `mcp`, `chronicle`, `correct`, `slumber`.
- Artifact strategy: explicit separation of public code, private
  vault, and rebuildable indices.
- Structured `health` check with `[ok] / [warn] / [fail]` tiers and
  actionable `next:` hints.

## [0.2.0] — 2026-04-29 — Retrieval freeze

- Locked hybrid retrieval baseline (Dense + BM25 + Tapestry/PPR +
  RRF + ACT-R) with Eternal Mirror benchmark.
- Recall@1 0.196 / R@5 0.582 / R@10 0.870 / MRR 0.358.

## [0.1.0] — 2026-04-21 — Personal memory infrastructure

- Initial release: Spring ritual, Vault layout (10/20/30/40/50),
  Oracle of Mneme enrichment, Tapestry graph, Chronicle access log.
