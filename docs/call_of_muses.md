# The Call of the Muses — Proactive Memory Gap Questions

> v1.0 headline feature. The Muses stop waiting for memories to arrive —
> they ask for them.

Most personal-memory systems are passive: they only know what you happen
to write down. The Call of the Muses inverts this. Memosyne scans the
Vault for what it *doesn't* know about you, and asks a few questions a
day to fill those gaps. Answers flow through the standard Spring Ritual
(ingest → enrich → vectorize), so they become first-class memories.

```
$ memosyne call

🎶 The Call of the Muses
   The Muses have 3 question(s) for you today.

[1/3] 🎭 Polyhymnia — Singer of Identity
      What principles or values do you refuse to compromise on?
  > ...
```

## Gap sources

All gap analysis is **deterministic** — no LLM, no vector index, no
network. It works on a fresh clone with zero configuration.

| Kind | What it detects | Example question |
|------|-----------------|------------------|
| `silent_muse` | A whole domain folder is empty (`10_Profile`, `30_Journal`, `40_Projects`, `50_Knowledge`) | "The Codex is nearly empty — tell me who you are." |
| `profile_topic` | The Codex (`10_Profile`) lacks a standard identity topic: origins, family, work history, food, values, goals... (14 topics, keyword scan) | "What foods do you love, and what would you never eat?" |
| `thin_person` | A person exists in the Tapestry but has ≤ 2 edges — mentioned once, then silence | "You've mentioned Alex — who are they to you?" |
| `temporal_gap` | A month in the last 6 with zero journal entries | "Your journal is silent for 2026-03 — what was happening then?" |
| `stale_domain` | A domain with data but no new file for 30+ days | "No project updates in a while — what are you building?" |

`20_AI_Chats` is deliberately excluded — conversations accumulate
passively; the Muses never ask you to "write more chats".

If Kuzu / the Tapestry is unavailable, `thin_person` degrades silently
to zero questions; everything else still works.

## The question ledger

Every asked / answered / skipped question is appended to
`muse_call_ledger.jsonl` (a private artifact, stored next to your other
vault artifacts). Stable question ids (`profile_topic:family`,
`temporal_gap:2026-03`, ...) drive cooldowns:

| Status | Cooldown | Rationale |
|--------|----------|-----------|
| answered | 120 days | don't re-interrogate known ground |
| skipped | 14 days | "not now" — retry in two weeks |
| asked (no answer) | 2 days | interrupted session — resurface soon |

`answered` is terminal: a later `asked`/`skipped` on the same id never
shortens its cooldown.

Question selection is deterministic per day (priority order, with a
date-seeded shuffle inside each priority tier), so repeated `--list`
calls on the same day show the same questions.

## CLI

```bash
memosyne call                  # interactive daily ritual (3 questions)
memosyne call --count 5        # more questions
memosyne call --lang zh        # Traditional Chinese questions
memosyne call --ingest         # auto-run the Spring Ritual afterwards

memosyne call --list           # print today's questions, no recording
memosyne call --list --json    # machine-readable (agents / cron)
memosyne call --answer profile_topic:family --text "..."   # one-shot answer
memosyne call --stats          # asked/answered/skipped + open gap counts
```

Environment:

```bash
MEMOSYNE_LANG=zh           # default question language (en)
MEMOSYNE_CALL_COUNT=3      # default daily question count
```

Answers land in `spring/muse_call_YYYY-MM-DD.md` (one file per day,
multiple sessions append) with `type: journal` frontmatter. The Oracle's
enrichment then extracts `personal_facts`, entities, and themes from
them like any other memory — which is how an answer about your family
ends up strengthening the Tapestry, not just sitting in a Q&A file.

## MCP tools (agent-driven interviews)

Two tools let any MCP-connected agent (Claude Desktop, Cursor, ...) run
the ritual conversationally:

- `muse_call(count, lang)` — returns today's questions as JSON and
  records them as `asked`. Scope: `read` (the ledger append follows the
  same precedent as `search_memory` writing the Chronicle).
- `muse_answer(question_id, answer)` — records the user's verbatim
  answer into `spring/` and marks the question `answered`. Scope:
  `write`.

A typical agent flow: fetch 1–3 questions at the start of a daily
check-in, weave them naturally into conversation, submit answers
verbatim, then remind the user to run `memosyne ingest` (or run it via
their own shell access).

## A daily habit

The feature is designed to be cron-able:

```bash
# Every evening, list today's questions in your terminal MOTD / notifier
0 21 * * * cd ~/path/to/memosyne && python memosyne.py call --list
```

Or simply make `memosyne call` part of the same routine as
`memosyne slumber` — the Muses ask, the Oracle weaves, the Vault grows.
