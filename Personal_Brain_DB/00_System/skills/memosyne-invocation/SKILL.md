---
name: memosyne-invocation
description: >-
  Systematic protocol for when and how to call the Memosyne personal memory
  database. Use this skill whenever the user asks about their past, mentions
  specific people/places/events from their life, reports a memory error, or
  records something new worth remembering. Do NOT use for general knowledge
  questions, pure reasoning, or disposable small talk.
---

# The Invocation — Memosyne Memory Protocol

> 神話定位：The Invocation（召喚儀式）—— 不是胡亂呼喊女神，是依循儀軌呼喚正確的 Muse。

This skill governs all agent interactions with the Memosyne personal memory
vault via its MCP server. It is **project-agnostic** — the same protocol
applies whether the user is drafting an email, debugging code, or
reminiscing over dinner.

---

## 0. The Cardinal Rule

**Memory is the user's autobiography. Every read is a consultation; every
write is a vow.**

- Reads must be surgical — don't dump results; interpret and quote sparingly.
- Writes must be deliberate — never apply without the user's explicit
  go-ahead. Default `apply=False` (dry-run) and show the diff first.
- When in doubt, **ask** rather than speculate from training data.

---

## 1. Behavior Taxonomy — Six Invocation Classes

Classify every candidate memory interaction into exactly one of six classes.
If the request doesn't fit, it's probably not a memory operation.

### 1.1 READ-RECALL — User explicitly asks about their past
- **Trigger phrases (EN)**: "when did I…", "what did I say about…",
  "remind me about…", "last time I…", "who did I meet at…"
- **Trigger phrases (ZH)**: 「我上次…」「我之前有沒有說過…」「什麼時候…」
  「還記得…」「那個 X 是什麼時候」
- **Tool**: `search_memory(query, top_k=5, auto_route=true)`
- **Follow-up**: if the match is promising but context is thin, call again
  with `return_parent=true` for the full parent section.

### 1.2 READ-CONTEXT — Agent proactively pulls memory to improve its answer
- **Trigger**: user mentions a proper noun (person / place / project) that
  smells personal, even if they didn't ask for recall. Examples:
  - "幫我寫封信給 friend-A" → you need to know who friend-A is first
  - "review my Tokyo plan" → search for prior Tokyo-related memory
- **Tool**: `search_memory(query=<entity_or_topic>, top_k=3, auto_route=true)`
- **Bias**: keep `top_k` small (3 or less); you're enriching context, not
  summarizing.
- **Silent mode**: do not announce "I searched the memory." Use what you
  found invisibly, cite only if you quote directly.

### 1.3 READ-TEMPORAL — Time-anchored query
- **Trigger**: "as of 2023", "before the move", "during the Tokyo trip",
  「2023 年時我…」「那段時期…」
- **Tool**:
  - For a point-in-time snapshot: `query_memory_at_time(query, timestamp)`
  - For an entity's full history: `get_entity_timeline(entity)`
- **Caveat**: temporal queries lean on the Bi-temporal Tapestry — pass
  ISO-like strings ("2023-06" or "2023-06-15") not natural language.

### 1.4 WRITE-INGEST — New memory entering the vault
- **Trigger**: user pastes / writes something clearly autobiographical that
  isn't already in the vault. Phrases: "記一下：…"、"log this:"
- **Tool**: **None directly** — this is the Spring Ritual's job. Tell the
  user to drop the file in `spring/` and run `python3 00_System/ingest.py`.
  Agent does NOT write raw memory files inline.
- **Why no direct tool**: ingestion runs Oracle enrichment, chunking,
  embedding, Tapestry weaving — a 30-second pipeline, not a single call.

### 1.5 WRITE-CORRECT — User fixes an error in existing memory
- **Trigger**: "that's wrong, it was X not Y", "remove that fact",
  「那個記錯了…」「應該是…」
- **Tool decision tree**:
  - Adding a new fact → `aletheia_add_fact(path, fact, apply=false)`
  - Replacing a fact → `aletheia_update_fact(path, old, new, apply=false)`
  - Deleting a wrong fact → `aletheia_invalidate_fact(path, match, apply=false)`
  - Fixing body typo → `aletheia_correct_text(path, old, new, apply=false)`
  - Undoing a prior correction → `aletheia_revert(log_id, apply=false)`
- **Flow**: always dry-run first (`apply=false`) → show diff → confirm →
  re-call with `apply=true`.
- **Post-apply**: remind the user that body changes (`correct_text`) queue
  a re-embed; suggest `python3 vectorize.py --rebuild` after a batch.

### 1.6 WRITE-ANNOTATE — Add a note without changing facts
- **Status**: reserved. No dedicated tool yet; currently use
  `aletheia_add_fact` with a fact prefixed like `"[note] …"`.

---

## 2. When NOT to Invoke

These patterns waste tokens, pollute the access log, or risk leaking
personal info into unrelated contexts:

- ❌ General knowledge: "what is quicksort" → answer from training.
- ❌ Pure reasoning: "what's 17 × 23" → compute, don't search.
- ❌ Disposable small talk: "morning!", "thanks", "ok" → no-op.
- ❌ Code / system help that doesn't reference the user's life.
- ❌ Anything inside code blocks the user is editing — don't search based
  on variable names.
- ❌ Confirming something the user just told you in this turn. Only search
  when the reference crosses sessions or is older than working memory.

If uncertain between "maybe relevant" and "probably not," prefer **no call**.
A missed recall is a small loss; a wrong recall pollutes the ACT-R
activation log and degrades future retrieval.

---

## 3. Query Hygiene

Before passing `query` to `search_memory`, strip:
- Politeness scaffolding: "可以幫我查一下…" → "…"
- Meta-framing: "I want to know when…" → extract the referent
- Typos the user clearly meant to fix (but keep proper nouns verbatim)

Do **not** translate the query. Memosyne's embedding model handles
zh-Hant / en / ja natively; translation erases nuance.

For complex queries (≥3 facets of time/person/place/action), consider
passing `decompose=true` to `search_memory` (when supported via the
underlying `vectorize.search()`).

---

## 4. Post-Retrieval Norms

### 4.1 Interpret, don't dump
- Synthesize 1–2 sentences: "根據 2023 年的手札，你那時…"
- Quote the exact phrase only when factuality matters (names, dates,
  numbers, exact quotes).

### 4.2 Cite sparingly
- One-shot citation format: `(30_Journal/2023/230611.md)`
- Don't list all 5 results. Mention the top 1–2 that actually matter.

### 4.3 When results are empty
- Say so honestly: "記憶庫裡查不到 X 的相關紀錄。"
- Don't fabricate. Don't guess from training.

### 4.4 When results contradict
- Show both; ask the user which is current. This is Aletheia territory.

---

## 5. Safety & Reversibility

- **All Aletheia ops default `apply=false`.** Never flip to true
  without the user's explicit yes in THIS turn.
- **High-risk `aletheia_correct_text`** (long / cross-line / big length
  delta) will refuse without a confirmation flag at CLI level; in MCP
  context, the user's "yes, confirm" in chat is sufficient.
- **Snapshots**: every apply shadow-copies the file to
  `aletheia_backup/`; `aletheia_log.jsonl` keeps before/after. Tell the
  user this if they hesitate to confirm.
- **Revert**: any applied op has a `log_id` — `aletheia_revert(log_id)`
  undoes it.

---

## 6. Decision Flowchart (at-a-glance)

```
user utterance
  ├─ general knowledge / reasoning / small talk → no memory call
  ├─ asks about personal past ─────────────────→ READ-RECALL
  ├─ mentions personal entity in passing ──────→ READ-CONTEXT (quiet)
  ├─ time-anchored question ───────────────────→ READ-TEMPORAL
  ├─ pasting new autobiographical content ─────→ WRITE-INGEST (via Spring)
  ├─ correcting existing memory ───────────────→ WRITE-CORRECT (Aletheia, dry-run first)
  └─ adding a note without changing facts ─────→ WRITE-ANNOTATE (reserved)
```

---

## 7. Meta-Tool

If the above still leaves the right call ambiguous, invoke
`memosyne_guide(situation)` — a self-describing meta-tool that returns
the recommended MCP tool + params for a natural-language situation.
Use it **sparingly** (not every turn); it's a fallback, not a substitute
for this skill.

---

## Appendix A — Current MCP Tool Inventory

| Tool | Class | Notes |
|---|---|---|
| `search_memory` | READ-RECALL / READ-CONTEXT | main hybrid search |
| `query_memory_at_time` | READ-TEMPORAL | bi-temporal point query |
| `get_entity_timeline` | READ-TEMPORAL | entity history |
| `get_profile` | READ (Profile) | static 10_Profile/ read |
| `list_journals` | READ (Journal) | directory listing |
| `read_file` | READ (raw) | last resort — prefer search |
| `get_memory_health` | ADMIN | stats / sanity check |
| `optimize_memory` | ADMIN | rebuild / consolidate |
| `aletheia_add_fact` | WRITE-CORRECT | `apply=false` default |
| `aletheia_update_fact` | WRITE-CORRECT | substring match |
| `aletheia_invalidate_fact` | WRITE-CORRECT | removes fact, log kept |
| `aletheia_correct_text` | WRITE-CORRECT | body literal replace |
| `aletheia_revert` | WRITE-CORRECT | undo by log_id |
| `memosyne_guide` | META | fallback when unsure |

---

## Appendix B — Mythic Anchors (optional CLI voice)

When the agent surfaces memory in chat, these phrasings echo the project's
Mnemosyne theme without getting in the way:

- Successful recall: 「從 Tapestry 中尋回 X。」
- Empty result: 「The waters are still. 沒有找到 X。」
- Ambiguous match: 「Two Muses whisper different stories; 請確認是哪段記憶。」
- Applied Aletheia: 「Aletheia 已揭真；log_id=…」
- Revert: 「Lethe 退回其水；原狀已復。」
