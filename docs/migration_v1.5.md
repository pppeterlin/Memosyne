# Upgrading to 1.5

1.5 moves the code into a normal Python package (`src/memosyne/`), takes the
private vault out of the repository tree, and makes the heavy retrieval
stages opt-in. An existing vault keeps working in place; nothing in it is
rewritten.

## 1. Update the checkout

```bash
git checkout master && git pull
```

Git removes the old symlinks and the submodule entry. You will see

```
warning: unable to rmdir 'Personal_Brain_DB/_vault': Directory not empty
```

That is expected: your vault folder stays on disk, with any uncommitted
work intact, and is now ignored by this repository. Memosyne still finds it
there (and the indexes next to the old code) without any configuration.

## 2. Reinstall

Pick the layers you actually use ([architecture.md](architecture.md)):

```bash
uv sync --extra search            # core + dense search
uv sync --extra all               # everything, including memosyne.labs
# or, with pip:
pip install -e ".[search]"
```

Then check that everything is found:

```bash
memosyne health
```

## 3. Decide which behaviour you want

Two defaults changed. Both can be switched back in `.env`:

| Change | 1.5 default | Restore the old behaviour |
|---|---|---|
| Search stages | dense + BM25 fusion only | `MEMOSYNE_SEARCH_PROFILE=full` |
| AI chat transcripts | searched only on request (`--include-chats`) | `MEMOSYNE_SEARCH_CHATS=1` |
| MCP tools | 9 core tools | `MEMOSYNE_MCP_TOOLS=all` |

`MEMOSYNE_SEARCH_PROFILE=full` with `MEMOSYNE_SEARCH_CHATS=1` runs the same
pipeline as 1.0. (Rankings will not match 1.0 result for result: its graph
stage ordered tied memories arbitrarily, so 1.0 itself gave different
rankings from one run to the next. 1.5 makes that order stable.) The reason
for the new default is in [evaluation.md](evaluation.md#ablation-v15).

## 4. Refresh the index and build the digest

```bash
memosyne rebuild      # incremental; also drops chunks that were never memories
memosyne digest       # timeline + entity index under <vault>/_digest/
```

Before 1.5, report files and loose files at the vault root could be indexed
as if they were memories. The first incremental rebuild removes them.

## 5. Update commands and client configs

| Before | Now |
|---|---|
| `python3 Personal_Brain_DB/00_System/ingest.py` | `memosyne ingest` |
| `python3 …/vectorize.py --query "…"` | `memosyne search "…"` |
| `python3 …/vectorize.py --rebuild` | `memosyne rebuild --full` |
| `python3 …/enrich.py`, `slumber.py`, `tapestry.py`, `mneme_weight.py` | `memosyne enrich`, `slumber`, `tapestry`, `chronicle` |
| `python3 …/aletheia.py`, `augury.py`, `chat.py` | `memosyne correct`, `audit`, `chat` |
| `python3 …/search.py` (REPL) | removed — use `memosyne search` |
| `process_files.py`, `watch.py` | removed — `memosyne ingest` replaced them |
| MCP client `args: ["…/mcp_server.py"]` | `memosyne mcp --print-config` prints the new snippet |

Every module is also runnable as `python -m memosyne.<module>`; the old-to-new
module names are listed in [architecture.md](architecture.md#module-map).

## 6. Move personal vocabulary into the vault

Journal auto-tags used to come from a keyword list in the code. The code now
ships only generic defaults; your own words (names, places, employers) go in
`<vault>/tags.yaml`:

```yaml
寵物: ["Saturn", "貓"]
旅行: ["東京", "機票"]
```

## Optional: adopt the new layout

Nothing forces this, but it is cleaner to keep the vault outside the
checkout entirely:

```bash
mv Personal_Brain_DB/_vault ~/memosyne-vault
mkdir -p ~/memosyne-vault/_artifacts
mv Personal_Brain_DB/00_System/chroma_db ~/memosyne-vault/_artifacts/
# move the remaining index/cache/log files from the vault root and
# Personal_Brain_DB/00_System into ~/memosyne-vault/_artifacts/ as well
```

```bash
# .env
MEMOSYNE_VAULT_DIR=~/memosyne-vault
MEMOSYNE_ARTIFACT_DIR=~/memosyne-vault/_artifacts
```

`memosyne health` shows which artifacts are found where. Anything missing
can be rebuilt with `memosyne rebuild --full`, except the two usage logs
(`chronicle.jsonl`, `muse_call_ledger.jsonl`) — move those, do not lose them.

### If your vault was the git submodule

After the upgrade the vault is still a working git repository, but its git
data lives inside the Memosyne checkout (`.git/modules/…`). To make it a
self-contained repository before moving it, run this **inside the vault
folder**:

```bash
cd Personal_Brain_DB/_vault
gitdir=$(git rev-parse --absolute-git-dir)   # …/.git/modules/Personal_Brain_DB/_vault
git config --unset core.worktree
rm .git                                      # a one-line pointer file, not a folder
mv "$gitdir" .git
git status                                   # history and uncommitted work are intact
```
