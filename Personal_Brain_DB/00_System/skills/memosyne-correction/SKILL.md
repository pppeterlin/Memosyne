---
name: memosyne-correction
description: >-
  Apply Aletheia correction operations to fix wrong, outdated, or
  contradictory facts in the Memosyne vault. Use when the user says "that
  fact is wrong", "update the record", "we agreed X actually means Y", or
  reports a contradiction between what memory says and what's true now.
  All operations are dry-run by default; never apply without explicit
  user confirmation.
---

# Aletheia — The Unconcealing of Truth

> 神話定位：Ἀλήθεια — 遮蔽之物的揭顯。Correcting memory is not deletion; it is
> the act of letting what is true emerge from what was once recorded.

This skill governs how to fix wrong content in already-ingested memories.
Every correction is logged; nothing is silently overwritten.

---

## 0. The Cardinal Rules

- **`apply=False` is the default.** Always run dry-run first. Only set
  `apply=True` after showing the diff and getting explicit user go-ahead.
- **Corrections are append-only in spirit.** Aletheia logs every change
  to `aletheia_log.jsonl` so `aletheia_revert` can undo by `log_id`.
- **Don't correct what you can't see.** Read the file first
  (`read_file(path)`) to confirm the current content matches your
  `old=` parameter.

---

## 1. Operation chooser

| User intent | Tool | Apply order |
|---|---|---|
| Add a fact that the original missed | `aletheia_add_fact(path, fact)` | dry-run → confirm → apply |
| Replace one specific value with another | `aletheia_update_fact(path, old, new)` | read → dry-run → confirm → apply |
| Mark a fact as no longer true (don't delete) | `aletheia_invalidate_fact(path, match)` | dry-run → confirm → apply |
| Fix free-text body content | `aletheia_correct_text(path, old, new)` | read → dry-run → confirm → apply |
| Undo a previous correction | `aletheia_revert(log_id)` | read log → dry-run → confirm → apply |

---

## 2. Standard flow

```
1. User: "actually friend-A lives in Osaka now, not Tokyo"
2. You: identify candidate memories
     search_memory("friend-A Tokyo", top_k=5)
3. You: for each candidate, read_file(path) to confirm content
4. You: propose Aletheia operation per file, all apply=False
5. Show user: { path, op, before, after } as a diff table
6. User confirms which to apply (may be subset)
7. You: re-run with apply=True for confirmed only
8. You: schedule rebuild of just the affected subset
     memosyne rebuild --paths <changed paths>
9. Verify: search_memory("Osaka friend-A") should now surface the
   corrected memory
```

---

## 3. What NOT to correct

- **Past states that were true at the time.** "Friend-A used to live in
  Tokyo, now Osaka" — use `aletheia_add_fact` for the new state, leave
  the old as historical record. Memory is autobiography, not a current
  snapshot.
- **Subjective phrasings.** "I said the meeting was bad but I actually
  enjoyed it" — this is a new reflection, not a correction. Ingest as a
  new memory instead.
- **Anything you're inferring from training data.** Only correct what the
  user explicitly tells you is wrong.

---

## 4. After-correction hygiene

- **Re-embed the changed files** so retrieval reflects the new content.
  `memosyne rebuild --paths <a> <b>` or full rebuild if many.
- **Re-weave the Tapestry** if entities changed.
- **Note in your reply** the `log_id` so the user can revert if needed.

---

## 5. Reverting

`aletheia_revert(log_id, apply=False)` shows what the revert would
restore. Apply only after the user confirms — reverts are themselves
logged, so the trail compounds.

---

## 6. When NOT to use Aletheia

- Adding a brand new memory → `memosyne-ingest`
- Reading existing memories → `memosyne-invocation` / `memosyne-search`
- Recurring memory cleanup → `memosyne-slumber`
