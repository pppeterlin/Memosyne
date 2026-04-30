# Memosyne Operations

This document is the day-to-day runbook for a local Memosyne workspace. It uses synthetic examples only; keep machine-specific paths, private virtualenv names, secrets, and personal vault details in ignored local override files.

## Environment

Use the project virtualenv before running operational commands:

```bash
python --version   # Python 3.10+
python memosyne.py health
```

If `memosyne` was installed in editable mode:

```bash
pip install -e .
memosyne health
```

Do not diagnose Memosyne with an unrelated system `python3`; it may not have the project dependencies.

## Daily Commands

```bash
# Check readiness
python memosyne.py health

# Drop files into The Spring, then ingest
python memosyne.py ingest

# Search
python memosyne.py search "測試查詢" --top 5

# Rebuild retrieval indexes
python memosyne.py rebuild

# Check MCP importability
python memosyne.py mcp --check

# Print MCP client config
python memosyne.py mcp --print-config

# Chronicle stats
python memosyne.py chronicle --stats

# Slumber stats
python memosyne.py slumber --stats
```

The root `memosyne.py` CLI is intentionally a thin v0.3 wrapper over existing scripts. It stabilizes the command surface without forcing a large internal refactor.

Related docs:

- [Configuration](configuration.md)
- [MCP setup](mcp.md)

## Health Checks

Human-readable:

```bash
python memosyne.py health
```

Machine-readable:

```bash
python memosyne.py health --json
```

Statuses:

| Status | Meaning | Action |
|---|---|---|
| `ok` | Ready or present | No action |
| `warn` | Non-blocking issue | Read the `next:` hint and fix before the related workflow |
| `fail` | Blocking issue | Fix before using search, ingest, or MCP |

Expected warnings on a private workstation can include:

- Ollama not running when you are not enriching or using local chat.
- Root-level private files present while developing locally. These must not be published.

## Common Fixes

### Wrong Python

Symptom:

```text
[fail] Python 3.8 ... requires 3.10+
```

Fix:

```bash
# Activate the project virtualenv, then rerun
python memosyne.py health
```

If the command still uses the wrong interpreter, call the virtualenv Python directly or reinstall the editable CLI inside the correct environment.

### Missing Dependencies

Symptom:

```text
[fail] import chromadb
[fail] import kuzu
```

Fix:

```bash
pip install -r Personal_Brain_DB/00_System/requirements.txt
python memosyne.py health
```

### Missing or Stale Private Vault

Symptom:

```text
[fail] The Vault ...
[warn] vault submodule ...
```

Fix:

```bash
git submodule update --init --recursive
python memosyne.py health
```

### Missing Retrieval Artifacts

Symptom:

```text
[fail] bm25_index ...
[warn] Chroma DB ...
```

Fix:

```bash
python memosyne.py rebuild
python memosyne.py health
```

### Chronicle SQLite Needs Rebuild

Chronicle uses `chronicle.jsonl` as the append-only source log. SQLite is a derived cache.

```bash
python memosyne.py chronicle --rebuild-db-from-jsonl
python memosyne.py chronicle --stats
```

### MCP Import Fails

Symptom:

```text
[fail] MCP server import failed: No module named 'mcp'
```

Fix:

```bash
pip install -r Personal_Brain_DB/00_System/requirements.txt
python memosyne.py mcp --check
```

### Ollama Is Not Running

Symptom:

```text
[warn] Ollama API ...
```

Fix only when you need enrichment, contextualization, HyQE, or local chat:

```bash
ollama serve
python memosyne.py health
```

## Artifact Policy

The v0.3 artifact strategy separates source logs from derived caches:

| Artifact | Role |
|---|---|
| `chronicle.jsonl` | append-only Chronicle source log |
| `chronicle.db` | SQLite cache derived from JSONL |
| `bm25_index.pkl` | derived sparse retrieval index |
| `contextual_cache.json` | LLM contextual note cache |
| `hyqe_cache.json` | LLM hypothetical question cache |
| `tapestry_db/` | derived graph store |
| `muse_centroids.json` | derived routing data |
| `chroma_db/` | local runtime vector cache |

Private artifacts belong in the private vault or local ignored paths. Public docs and examples must use synthetic queries and generic paths.
