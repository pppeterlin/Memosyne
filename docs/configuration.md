# Memosyne Configuration

Memosyne v0.3 still keeps most defaults in code, but the operational boundary is now explicit: use a project virtualenv, keep private machine settings out of public docs, and use environment variables for runtime overrides.

## Python Environment

Use Python 3.10+ in a project-specific virtualenv:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r Personal_Brain_DB/00_System/requirements.txt
pip install -e .
memosyne health
```

Do not use an unrelated system `python3` to diagnose the project. `memosyne health` reports the actual interpreter it is running under.

## Paths

Default paths are relative to the repo root:

| Path | Purpose |
|---|---|
| `spring/` | The Spring: drop zone for incoming files |
| `Personal_Brain_DB/00_System/` | scripts, local runtime caches, compatibility symlinks |
| `Personal_Brain_DB/_vault/` | private vault submodule and private portable artifacts |
| `Personal_Brain_DB/00_System/chroma_db/` | local Chroma vector cache |

Artifact path overrides:

| Variable | Default | Use |
|---|---|---|
| `MEMOSYNE_VAULT_DIR` | `Personal_Brain_DB/_vault` | relocate the private vault/artifact root |
| `MEMOSYNE_ARTIFACT_DIR` | same as vault dir | relocate generated private artifacts only |

These should usually live in a private `.env` or local shell profile, not in public docs.

## Runtime Artifacts

| Artifact | Default | Source or cache |
|---|---|---|
| `chronicle.jsonl` | `_vault/chronicle.jsonl` | append-only Chronicle source log |
| `chronicle.db` | `_vault/chronicle.db` via `00_System` symlink | SQLite cache derived from JSONL |
| `bm25_index.pkl` | `_vault/bm25_index.pkl` via symlink | derived retrieval index |
| `contextual_cache.json` | `_vault/contextual_cache.json` via symlink | LLM cache |
| `hyqe_cache.json` | `_vault/hyqe_cache.json` via symlink | LLM cache |
| `tapestry_db/` | `_vault/tapestry_db/` via symlink | derived graph store |
| `muse_centroids.json` | `_vault/muse_centroids.json` via symlink | derived routing data |
| `chroma_db/` | `00_System/chroma_db/` | local runtime vector cache |

Chronicle rebuild:

```bash
memosyne chronicle --rebuild-db-from-jsonl
```

Retrieval rebuild:

```bash
memosyne rebuild
```

## Models

| Setting | Current default |
|---|---|
| Embedding | `paraphrase-multilingual-MiniLM-L12-v2` |
| Enrichment | script default, usually an Ollama model |
| Contextual / HyQE | `gemma3:4b` |
| Local chat | `gemma4:26b` |
| Cloud chat | Gemini via `google-genai` |
| Proxy backend | OpenAI-compatible proxy via `proxy:` model prefix |

Model names are currently passed through script flags or code defaults. v0.3 documents the configuration surface without forcing a config-file refactor.

## Environment Variables

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | Ollama endpoint, default `http://127.0.0.1:11434` |
| `MEMOSYNE_HF_OFFLINE` | default `1`; avoids Hugging Face network checks after models are cached |
| `LLM_PROVIDER` | optional forced provider: `ollama`, `openrouter`, or `proxy` |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `OPENROUTER_KEY` | fallback OpenRouter key name |
| `OPENROUTER_BASE_URL` | optional OpenRouter-compatible base URL |
| `PROXY_API_KEY` | key for an OpenAI-compatible proxy |
| `PROXY_BASE_URL` | proxy endpoint override |
| `ANTHROPIC_API_KEY` | fallback proxy key name |
| `CHAT_CATEGORY_KNOWLEDGE_PENALTY` | experimental AI-chat rerank penalty; default `1.0` |

Use `.env.example` as a public template and keep real values in ignored local files.

## Privacy Boundary

Public repo:

- generic commands
- synthetic queries
- sample configuration with placeholders
- architecture and operations docs

Private/local only:

- real vault content
- actual API keys
- machine-specific paths
- personal virtualenv names
- private benchmark queries
- generated artifacts derived from private memories
