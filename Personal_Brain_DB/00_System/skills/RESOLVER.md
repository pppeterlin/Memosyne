# The Codex of Skills — Memosyne Resolver

> 神話定位：The Codex — 召喚正確繆思的索引。Agents should not memorize protocol; they
> should consult this index, then load the skill that matches.

This file maps user intents and trigger phrases to the right Memosyne
skill. An agent (Claude Code, Cursor, custom MCP client) should consult
this resolver **first**, then load the matched skill file in full.

If multiple skills match, load all of them. Skills are designed to be
compositional — `memosyne-correction` and `memosyne-search` together
cover a "find the wrong fact and fix it" flow.

---

## The Six Skills

| Skill | Purpose | Path |
|---|---|---|
| `memosyne-invocation` | When and whether to call memory at all | `skills/memosyne-invocation/SKILL.md` |
| `memosyne-ingest` | Add new memories via the Spring ritual | `skills/memosyne-ingest/SKILL.md` |
| `memosyne-search` | Search strategy: queries, walks, top-k | `skills/memosyne-search/SKILL.md` |
| `memosyne-correction` | Fix wrong / outdated facts via Aletheia | `skills/memosyne-correction/SKILL.md` |
| `memosyne-slumber` | Consolidation: reflection, Hebbian, Lethe | `skills/memosyne-slumber/SKILL.md` |
| `memosyne-augury` | Retrieval evaluation and regression replay | `skills/memosyne-augury/SKILL.md` |

---

## Trigger phrase → Skill

| Phrase pattern (EN / ZH) | Skill(s) to load |
|---|---|
| "when did I…", "what did I say about…", 「我上次…」, 「我之前…」 | `memosyne-invocation`, `memosyne-search` |
| "remember this", "save this", 「記下這個」, 「存進記憶」 | `memosyne-ingest` |
| "ingest", "process spring", 「處理 spring」 | `memosyne-ingest` |
| "that's wrong", "actually X is Y", 「那個錯了」, 「應該是」 | `memosyne-correction` |
| "update the record", 「更新紀錄」 | `memosyne-correction` |
| "consolidate", "reflect on…", "run slumber", 「整理記憶」, 「反思」 | `memosyne-slumber` |
| "forget X", "archive old", 「歸檔」, 「忘掉」 | `memosyne-slumber` (Lethe ritual) |
| "did my change break recall?", "eval retrieval", 「跑評測」 | `memosyne-augury` |
| "replay queries", "regression test", 「回放查詢」 | `memosyne-augury` |
| User mentions a personal proper noun mid-conversation | `memosyne-invocation` (READ-CONTEXT path) |

---

## CLI verb → Skill

Every public `memosyne` subcommand has a primary skill:

| CLI verb | Primary skill | Secondary |
|---|---|---|
| `memosyne search` | `memosyne-search` | `memosyne-invocation` |
| `memosyne ingest` | `memosyne-ingest` | — |
| `memosyne rebuild` | `memosyne-ingest` (post-step) | — |
| `memosyne correct` | `memosyne-correction` | — |
| `memosyne slumber` | `memosyne-slumber` | — |
| `memosyne eval` | `memosyne-augury` | — |
| `memosyne query-log` | `memosyne-augury` | — |
| `memosyne mcp` | (transport — no skill needed) | — |
| `memosyne health` | (diagnostic — no skill needed) | — |
| `memosyne init` | (setup — no skill needed) | — |

---

## Decision flow

```
User says something
   │
   ├── Asks about their past / mentions personal entity?
   │     → memosyne-invocation (decide whether to search)
   │       → if yes, memosyne-search (decide how to search)
   │
   ├── Wants to add new content?
   │     → memosyne-ingest
   │
   ├── Says something already in memory is wrong?
   │     → memosyne-correction (always dry-run first)
   │
   ├── Wants maintenance / reflection?
   │     → memosyne-slumber
   │
   └── Working on retrieval code, asks about quality?
         → memosyne-augury
```

---

## Adding a new skill

1. Create `skills/memosyne-<name>/SKILL.md` with YAML frontmatter
   (`name`, `description`).
2. Append a row to the "Six Skills" table above (rename heading).
3. Add trigger phrases under "Trigger phrase → Skill".
4. If it corresponds to a CLI verb, add to "CLI verb → Skill".
5. Update `mcp_server.py::_INVOCATION_RULES` if the skill should be
   advisable via the `invocation_protocol` MCP tool.
