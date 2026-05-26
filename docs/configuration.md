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

| Setting | Notes |
|---|---|
| Embedding | A multilingual sentence-transformers model. The embedding model is the only model Memosyne ships with a hard expectation on; pick a model that matches the languages you write in. |
| Enrichment / Contextual / HyQE / Local chat | Any locally-served LLM you choose. Memosyne does not ship a default model — pass model identifiers via script flags or set them in your local config. |
| Cloud LLM (optional) | Any provider supported by `Personal_Brain_DB/00_System/llm_client.py`; see that file for the current list. |

Memosyne does not bundle, download, or assume any specific generative LLM. The local LLM runtime (Ollama, llama.cpp, LM Studio, vLLM, …) and the model itself are the user's choice. Public defaults in this repo intentionally avoid hard-coding specific model names.

## Environment Variables

| Variable | Purpose |
|---|---|
| `MEMOSYNE_HF_OFFLINE` | default `1`; avoids Hugging Face network checks after embedding models are cached |
| `LLM_PROVIDER` | optional forced provider — see `llm_client.py` for the current set |
| `CHAT_CATEGORY_KNOWLEDGE_PENALTY` | experimental AI-chat rerank penalty; default `1.0` |

Provider-specific variables (local endpoint host, cloud API keys, base URLs, etc.) depend on which backend you choose. The canonical list lives in `Personal_Brain_DB/00_System/llm_client.py` and `.env.example`. Avoid hard-coding provider-specific names in public documentation; treat them as examples, not requirements.

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
