# Changelog

All notable changes to Memosyne are recorded here. Format inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow the project's `vMAJOR.MINOR` release sequence.

## [Unreleased] — v1.0 The Call of the Muses

The first release aimed at people other than the author. Memosyne stops
being a passive archive: the Muses proactively interview you to fill
the gaps in your own memory vault.

### Added

- **The Call of the Muses (`memosyne call`)** — proactive memory-gap
  questions. Deterministic gap analysis over the Vault (no LLM, no
  index required) across five sources: empty domains, missing profile
  topics (14 identity topics), thin Tapestry persons (mentioned but
  unknown), silent journal months, and stale domains. Interactive
  daily ritual; answers land in `spring/` and flow through the
  standard ingest → enrich → vectorize pipeline.
- **Question ledger with cooldowns** (`muse_call_ledger.jsonl`,
  registered as a private ephemeral artifact) — answered questions
  rest for 120 days, skipped for 14, asked-but-unanswered for 2.
  `answered` is terminal and never shortened by later events.
- **`memosyne call --list --json` / `--answer QID --text` / `--stats`**
  — non-interactive surface for agents, cron jobs, and notifiers.
- **MCP tools `muse_call` / `muse_answer`** — any MCP-connected agent
  (Claude Desktop, Cursor, ...) can run the interview conversationally
  and submit verbatim answers. Scopes: read / write respectively;
  `muse_answer` is write-gated over HTTP.
- **`docs/call_of_muses.md`** — full feature documentation.
- **Bilingual questions** — `--lang en|zh` (or `MEMOSYNE_LANG`).

### Fixed

- **`memosyne quickstart` crashed at Step 3** — `_run_script()` did not
  accept the `env_overrides` keyword that quickstart passed, raising
  `TypeError` during the first-run demo. The first-run experience now
  completes.

## [Unreleased] — v0.7 The Open Threshold

Polish for adoption. Lowers the bar for a first-time user to install,
run a demo, and start trusting Memosyne with real memories. **Zero
changes** to the retrieval engine.

### Added

- **`memosyne quickstart`** — first-run experience. Detects available
  LLM providers, runs `eval --sample`, prints two example searches
  with the sample vault, then points at the real-vault workflow.
- **`memosyne providers list` / `memosyne providers test <name>`** —
  single-screen view of all four backends (ollama / openrouter /
  deepseek / proxy) with status markers and one-call connectivity
  test.
- **`memosyne enrich` / `contextualize` / `hyqe`** — first-class
  subcommands; daily ops no longer require
  `cd Personal_Brain_DB/00_System`.
- **`memosyne search --return-parent`** — exposes the small-to-big
  retrieval flag that the underlying search has supported since v0.5
  but was unreachable from CLI.
- **`scripts/release.sh X.Y.Z`** + `make release VERSION=X.Y.Z` — the
  Rite of Release. Pre-flight gates (clean tree, on master, in sync
  with origin, `make verify` green, tag-doesn't-exist) prevent the
  v0.5 "forgot to tag" incident from repeating.
- **DeepSeek official API as first-class provider** (already shipped
  in v0.6 commit but elevated by `providers list` in v0.7).
- **30-second start** in README + mermaid architecture diagram +
  mythology→engineering glossary.

### Changed

- **`memosyne rebuild`** defaults to incremental; `--full` opt-in.
  Pre-v0.7 it always passed `--rebuild` to vectorize.py, wiping
  21K+ chunks even for a single new file. Real-world ingest is
  10× faster now.
- **vectorize progress bar** uses tqdm (or throttled print fallback)
  instead of `\r`-spam that destroyed log file scrollback.
- **Ephemeral artifacts** (`dirty_paths.txt`, opt-in `query_log.jsonl`)
  no longer trigger health `warn` when absent — they're consumed and
  removed by design.
- **Ollama health hint** upgraded from "Start Ollama before…" to two
  explicit choices (start Ollama OR pick a cloud backend via
  `memosyne providers list`).
- **`spring/*.md` gitignored** with `!spring/README.md` exception.
  Personal drop-zone files were tracked by accident before; never
  again.
- **README roadmap** rewritten to reflect shipped v0.1–v0.6 + in-
  progress v0.7, removing the stale "Retrieval v2" punch list.

### Fixed

- **ingest dry_run no longer pollutes the turn ledger** (v0.6 backfill
  path missed a `dry_run` guard). The data written was correct but
  violated the dry-run contract.
- **ingest bare-name Gemini export warning** — when a `.md` looks
  like a Gemini export but lacks the `_<convhash>.md` suffix, ingest
  now prints exactly how to fix it instead of silently duplicating
  the conversation.

### Notes

- No retrieval-quality changes; v0.2 baseline still applies.
- Phase 3 partial enrichment merge (v0.6 deferred) still pending —
  next release.

## [0.6.0] — 2026-05-27 — Accumulating sources

### Added

- **Phase 0 — content_hash + conflict warn** — every ingest computes a
  stable `body_hash` (normalized sha256, frontmatter-stripped) and
  stamps it into frontmatter `content_hash`. When the same filename
  re-imports with different body, the router warns loudly and
  **does not archive the spring source**, breaking the pre-v0.6
  silent-drop failure mode.
- **Phase 1 — turn-aware Gemini update** — `turns.py` (Turn dataclass +
  3 parsers: Gemini / Claude / JournalAppend), `turn_ledger.py`
  (SQLite ledger of every turn ever ingested, sharing chronicle.db),
  `ingest.py::_try_turn_aware_gemini_update` (preserves uuid, bumps
  date_updated, clears enriched_at, records turn diffs).
  Re-importing a continued Gemini conversation now adds only the new
  turns to the ledger and triggers a clean vector chunk refresh.
- **Phase 2 — parser abstraction + journal append** — `TurnParser`
  protocol + `detect_parser()` registry. `route_journal` uses
  `JournalAppendParser` for journals with ≥2 `## YYYY-MM-DD` headings,
  giving the same incremental-update behavior as Gemini for journals
  that grow over time.
- **vectorize.refresh_paths(paths)** — deletes chunks by
  metadata.path, called automatically by `build_index` after consuming
  `dirty_paths.txt` written by ingest's update path. Fixes the
  long-standing "updated file's new chunks get filtered as already
  existing" silent failure.
- **IngestResult dataclass** — replaces `Optional[Path]` return type
  in all routers, separating `should_archive` from `needs_followup`
  so a conflict can leave the spring source in place.

### Changed

- `ingest.py` routers now return `IngestResult`; `main()` branches on
  `should_archive` / `needs_followup` rather than `if dst:`.
- `from __future__ import annotations` added to `ingest.py` so the
  new PEP 604 / generic-tuple annotations work on Python 3.8.
- `dirty_paths` registered as a proper artifact under
  `Personal_Brain_DB/00_System/dirty_paths.txt`.

### Deferred to v0.6.1 / v0.7

- Partial enrichment merge (current behavior: full re-enrich on
  update — correct but LLM-expensive for very large conversations).
- `memosyne health` check that frontmatter content_hash matches the
  actual body (utility exists; integration pending).
- Aletheia turn-level correction integration.

### Tests

- 28 new unit tests across content_hash, turns, turn_ledger, Phase 0
  conflict detection, and Phase 1 turn-aware update (total now 109).
- End-to-end smoke verified: insert → same-skip → continuation update
  (+2/4 turns) → same-skip — ledger and dirty marker behave as
  designed.

## [0.5.0] — v0.5 scale & self-eval

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
