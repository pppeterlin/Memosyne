# Memosyne Roadmap

> Scope: v0.2 retrieval freeze, v0.3 productization, v0.4 open-source release.
> This document is the release-level plan. `TODO_retrieval_v2.md` remains the task-level tracker.

## Positioning

Memosyne is a local-first personal memory infrastructure layer for AI agents.
Its core value is not one retrieval trick, but a full memory lifecycle:

- ingest personal material into a readable vault
- enrich it with ground-truth-preserving metadata
- retrieve with dense, sparse, graph, temporal, and cognitive signals
- correct wrong memories safely
- expose the memory layer through MCP
- evaluate retrieval changes before they ship

The next three releases should stop expanding the retrieval stack and instead
make the system stable, usable, and explainable.

## Release Sequence

| Release | Theme | Primary Outcome | Public Readiness |
|---|---|---|---|
| v0.2 | Retrieval Freeze | Lock the retrieval architecture and evaluation baseline | Internal |
| v0.3 | Productized Usage | Make daily use predictable through CLI, docs, and operations | Private beta / advanced users |
| v0.4 | OSS Release | Prepare a clean, installable, privacy-safe open-source project | Public |

## v0.2 — Retrieval Freeze

Detailed plan: [docs/v0.2_retrieval_freeze.md](docs/v0.2_retrieval_freeze.md)

Goal: freeze the current retrieval architecture and create a defensible baseline.

Key decisions:

- No new retrieval modules unless an evaluation shows a specific bottleneck.
- Keep the current hybrid architecture: Dense + BM25 + Tapestry/PPR + RRF + ACT-R.
- Treat Muse routing as an explicit top-5 optimization with known Recall@10 tradeoff.
- Require evaluation reports for retrieval changes.

Exit criteria:

- Latest Eternal Mirror report is committed and documented.
- Augury golden set exists with real user-level questions.
- Current `_vault` generated changes are either committed or intentionally ignored.
- TODO status matches the actual repository state.

## v0.3 — Productized Usage

Detailed plan: [docs/v0.3_productization.md](docs/v0.3_productization.md)

Goal: reduce daily operation friction.

Key decisions:

- Make `memosyne` a coherent command surface instead of many independent scripts.
- Keep mythological names in user-facing copy, but keep command behavior ordinary.
- Prefer reliable local operation over adding new cognitive features.
- Make failure modes explicit: missing model, missing Kuzu, stale index, dirty vault.

Exit criteria:

- A user can initialize, ingest, search, rebuild, check health, and run MCP from documented commands.
- Common maintenance tasks have one canonical path.
- The docs explain which parts call local models, cloud models, or no model.
- Runtime prerequisites are explicit and testable.

## v0.4 — OSS Release

Detailed plan: [docs/v0.4_oss_release.md](docs/v0.4_oss_release.md)

Goal: make the project safe and understandable for public release.

Key decisions:

- Do not publish private memory data, keys, caches, or personal benchmark reports.
- Ship a synthetic sample vault with realistic structure.
- Make privacy boundaries first-class documentation.
- Present Memosyne as a memory infrastructure project, not a generic chatbot.

Exit criteria:

- Public repo contains no private vault material or secrets.
- Sample vault supports the quickstart and demo commands.
- Installation path works from a clean machine.
- README explains value, architecture, privacy model, and limitations in under 10 minutes.

## Release Governance

Every release should define:

- `Scope`: what is allowed to change
- `Non-goals`: what is explicitly deferred
- `Validation`: commands or reports required before merge
- `Artifacts`: docs, reports, sample data, or package files expected at release

For retrieval work, the default validation command should be:

```bash
workon personal-memory
cd Personal_Brain_DB/00_System
make eval-ci
```

For operational work, the minimum validation should cover:

```bash
workon personal-memory
python Personal_Brain_DB/00_System/mneme_weight.py --stats
python Personal_Brain_DB/00_System/tapestry.py --stats
python Personal_Brain_DB/00_System/slumber.py --stats
```

## Recommended Current Priority

The immediate next work should be v0.2 closure:

1. Sync `TODO_retrieval_v2.md` with actual state.
2. Decide whether to commit the dirty `_vault` submodule updates.
3. Create a small Augury golden set.
4. Record the current retrieval baseline as the freeze point.

Only after that should v0.3 packaging and CLI work begin.
