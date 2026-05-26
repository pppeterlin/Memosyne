---
name: memosyne-ingest
description: >-
  Decision protocol for adding new memories to the Memosyne vault via the
  Spring ritual. Use when the user says they want to "save this", "remember
  this", "add to memory", drops a file into spring/, or shows you content
  that clearly belongs in their personal vault (journal, chat export,
  knowledge note). Do NOT use to read or correct existing memories — see
  memosyne-invocation / memosyne-correction.
---

# The Spring Ritual — Memosyne Ingest Protocol

> 神話定位：The Spring of Mnemosyne — 飲此泉者，靈魂記憶永不遺失。Every drop must reach
> the right Muse. Never let raw content seep directly into the Vault.

This skill governs how new content enters the Memosyne vault. The vault is
the user's autobiography; every ingestion is a permanent inscription.

---

## 0. The Cardinal Rules

- **Spring is the only door.** Never write to `Personal_Brain_DB/` directly,
  always drop the file in `spring/` and let `memosyne ingest` route it.
- **Spring is the user's intent surface.** Don't move files into `spring/`
  without the user confirming they want it ingested.
- **Originals are sacred.** After successful ingest, the original is moved
  to `spring/_processed/<YYYY-MM>/`. Don't delete; keep the trail.

---

## 1. When to invoke

| Signal | Action |
|---|---|
| User: "save this chat / journal / note" | Confirm path, drop in `spring/`, run `memosyne ingest` |
| User drops `.md` / `.pages` / `.txt` in `spring/` | Run `memosyne ingest` — it routes automatically |
| User pastes long content + "remember this" | Write to `spring/YYMMDD_<topic>.md` with minimal frontmatter, then ingest |
| User: "import my Gemini chats" | Ensure files are in `spring/`, ingest, then `memosyne rebuild` |
| User asks about an existing memory | Wrong skill — use `memosyne-invocation` |
| User wants to fix wrong content | Wrong skill — use `memosyne-correction` |

---

## 2. The Three Rituals (what `ingest` does)

1. **The Discernment** — `muses.py` classifies the file → routes to
   `20_AI_Chats/Gemini`, `30_Journal/<year>/`, `50_Knowledge/`, etc.
2. **The Weaving** — `enrich.py` calls the Oracle (LLM) to extract entities
   into YAML frontmatter (themes, people, locations, events, emotions).
   **Ground-Truth Preserving**: only what appears in the original text.
3. **The Inscription** — `vectorize.py` chunks the file and embeds it.

After ingest you usually want `memosyne rebuild` to refresh the BM25 index
and the Tapestry graph. `memosyne health` confirms everything wired up.

---

## 3. Source-specific notes

- **Gemini exports** (v0.6+): filename hash is the conversation id.
  Same content re-imported = `skipped_same` (no-op). Continuation
  (same file with appended turns) = turn-aware **update**: uuid
  preserved, only new turns added to the ledger, vector chunks
  refreshed. No more silent drops. If the body **diverges** in a way
  that isn't a clean continuation (e.g. edited earlier turns), ingest
  warns + leaves the spring source in place for manual triage.
- **Journal `.txt` / `.md`**: filename `YYMMDD_topic.md` is the convention.
  Frontmatter optional — `ingest.py` synthesizes one if missing.
- **`.pages` files**: extracted via `_extract_pages_text`. macOS only.
- **Knowledge notes**: include `type: knowledge` in frontmatter to route
  to Urania's domain (`50_Knowledge/`).

---

## 4. After-ingest verification

Always check:

```bash
memosyne health                    # all green?
memosyne search "<distinctive phrase from new content>" --top 3
```

If the search doesn't surface the new memory, the embedding likely didn't
build — run `memosyne rebuild` and re-test.

---

## 5. What can go wrong

- **Ollama not running** → enrichment skipped; file in vault has no YAML
  entities. Fix: start Ollama, then `memosyne ingest` again (re-runs enrich
  on unenriched files).
- **`spring/` original not archived** → check `spring/_processed/<YYYY-MM>/`;
  if missing, the ingest failed partway. Inspect logs.
- **Same filename, different content** (v0.6+): three sub-cases:
  - Gemini / journal-with-day-headings + appended turns → automatic
    turn-aware update (incremental, preserves uuid)
  - Other source or unparseable format → warn, spring source NOT
    archived, await manual triage
  - Genuinely different memory that shares a filename → user renames
    and re-ingests

See `docs/v0.6_accumulating_sources.md` for the full design and the
Phase 3 partial-enrichment work still pending.
