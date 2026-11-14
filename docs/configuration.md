# Configuration

Memosyne is configured with environment variables. Put them in
`<home>/.env` (loaded automatically; real environment variables win) or
export them in your shell. `.env.example` lists every one with comments.

Nothing needs to be set for a first run: `memosyne init` creates `spring/`
and `vault/` next to where you run it.

## Paths

| Variable | Default | Purpose |
|---|---|---|
| `MEMOSYNE_HOME` | the source checkout, else the current directory | project directory holding `.env`, `spring/` and the default vault |
| `MEMOSYNE_VAULT_DIR` | `<home>/vault` | where your Markdown memories live |
| `MEMOSYNE_ARTIFACT_DIR` | `<vault>/_artifacts` | derived files: indexes, caches, logs |
| `MEMOSYNE_SPRING_DIR` | `<home>/spring` | drop zone for files waiting to be ingested |

If you set only `MEMOSYNE_VAULT_DIR`, artifacts are written into the vault
root itself (the pre-1.5 behaviour). Set `MEMOSYNE_ARTIFACT_DIR` as well to
keep them in their own folder.

The vault is yours. Keep it outside the Memosyne checkout, or in its own
private repository — never in a public one.

## Search

| Variable | Default | Purpose |
|---|---|---|
| `MEMOSYNE_SEARCH_PROFILE` | `core` | `core` = dense (if installed) + BM25 fusion. `full` = also graph, PPR, backlink boost and ACT-R reranking from `memosyne.labs` |
| `MEMOSYNE_SEARCH_CHATS` | `0` | `1` = include AI chat transcripts in every search. Otherwise they are searched only on request (`--include-chats`, `include_chats=True`) |
| `MEMOSYNE_INDEX_CHATS` | `1` | `0` = leave AI chats out of the index entirely (much smaller, much faster to build); run `memosyne rebuild --full` after changing |
| `MEMOSYNE_WALK` | `deep` | *full profile:* graph walk — `deep` (PPR), `fast` (two-pass), `off` |
| `MEMOSYNE_BACKLINK_COEF` | `0.05` | *full profile:* backlink boost strength; `0` disables |
| `MEMOSYNE_ACTR_ALPHA` | `0.2` | *full profile:* ACT-R rerank weight; `0` disables |
| `MEMOSYNE_CHRONICLE_DECAY` | built-in | *full profile:* per-domain ACT-R decay overrides |

## Embeddings (search extra)

| Variable | Default | Purpose |
|---|---|---|
| `MEMOSYNE_EMBED_PROVIDER` | `local` | `local` (sentence-transformers), `ollama`, or `openai-compat` |
| `MEMOSYNE_EMBED_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | model name / id |
| `MEMOSYNE_EMBED_DIM` | — | declare the dimension to catch a silent model mismatch |
| `MEMOSYNE_EMBED_URL` | — | remote endpoint for `ollama` / `openai-compat` |
| `MEMOSYNE_EMBED_API_KEY` | — | only for public `openai-compat` endpoints |
| `MEMOSYNE_EMBED_DEVICE` | `auto` | local device: `auto`, `cpu`, `mps`, `cuda` |
| `MEMOSYNE_EMBED_BATCH` / `_TIMEOUT` / `_RETRIES` | `64` / `30` / `3` | remote request tuning |
| `MEMOSYNE_HF_OFFLINE` | `1` | use cached models without network checks; set `0` once to download |

The default model truncates input at 128 tokens. For long paragraphs prefer
a long-context model such as `BAAI/bge-m3` (`MEMOSYNE_EMBED_DIM=1024`).
After changing the embedding model run `memosyne rebuild --full`.

## LLM providers (enrichment)

Enrichment is the only core step that calls an LLM, and it is optional
(`memosyne ingest --no-enrich`). `memosyne providers list` shows what is
ready.

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API (default enrichment model `deepseek:deepseek-flash`) |
| `OPENROUTER_API_KEY` | OpenRouter (`openrouter:<model>`) |
| `PROXY_BASE_URL`, `PROXY_API_KEY` | any OpenAI-compatible endpoint (`proxy:<model>`) |
| `OLLAMA_HOST` | local Ollama (needs `pip install -e ".[ollama]"`) |
| `LLM_PROVIDER` | force one backend instead of resolving from the model name |

Cloud providers receive the text of the memories being enriched. See
[privacy.md](privacy.md) before enabling one.

## MCP server

| Variable | Default | Purpose |
|---|---|---|
| `MEMOSYNE_MCP_TOOLS` | `core` | `all` also exposes the labs tools (consolidation, graph timeline, point-in-time search, corrections) |
| `MEMOSYNE_AUTH_DB` | `~/.memosyne/tokens.sqlite` | bearer-token store for the HTTP transport |

## Other

| Variable | Default | Purpose |
|---|---|---|
| `MEMOSYNE_LANG` | `en` | language of gap questions (`en`, `zh`) |
| `MEMOSYNE_CALL_COUNT` | `3` | questions per `memosyne call` |
| `MEMOSYNE_CAPTURE_QUERIES` | `0` | *labs:* log real queries for replay |
| `MEMOSYNE_QUERY_SCRUB_TERMS` | — | extra terms to redact from captured queries |

## Vault-side configuration

Personal vocabulary belongs in the vault, not in code:

| File | Purpose |
|---|---|
| `<vault>/tags.yaml` | journal auto-tag keywords (`tag: [word, …]`). A tag listed here replaces the built-in list; an empty list switches it off |

## Artifacts

`memosyne health` lists every artifact, its path and whether it exists.
All of them are derived and can be rebuilt from the vault, except the two
append-only logs that record usage: `chronicle.jsonl` (access log) and
`muse_call_ledger.jsonl` (gap-question history).
