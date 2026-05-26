---
name: memosyne-slumber
description: >-
  Run the Slumber rituals — reflection (distill recent memories into
  insights), Hebbian learning (strengthen co-recalled edges), and the Lethe
  protocol (mark dormant memories without deleting). Use when the user
  says "do maintenance", "consolidate memory", "run slumber", or asks for
  insights distilled from recent activity. Do NOT use for one-off
  searches or corrections.
---

# The Rite of Slumber — Memosyne Consolidation Protocol

> 神話定位：The Rite of Slumber — 記憶在沉睡中鞏固，在遺忘中保留可能。Sleep is not
> idleness; it is when scattered impressions become structured knowledge.

This skill governs periodic memory maintenance. Slumber doesn't replace
ingest or search — it shapes what's already in the vault.

---

## 0. The Cardinal Rules

- **Reflection writes new memories** under `10_Profile/reflections/`.
  Don't run it without the user being ready to read what surfaces.
- **The Lethe Protocol marks dormant, never deletes.** Recoverable.
  Default `--dry-run` before any `--forget` apply.
- **Slumber is opt-in maintenance**, not always-on. Run it weekly or
  when the user explicitly asks; not after every ingest.

---

## 1. The Three Rituals

### 1.1 Reflection — `memosyne slumber --reflect --days N`

Distills recent memories (`N` days back) into high-level insights via LLM
synthesis. Output lands in `10_Profile/reflections/YYYY-MM-DD.md`.

- **When to run**: weekly, or after a notable life event the user wants
  processed.
- **Default `--days`**: 14. Smaller windows (3–7) for after-event
  reflection, larger (30+) for monthly retrospective.
- **What surfaces**: themes, recurring people, emotional patterns,
  unresolved threads. Not facts — the user already has those.

### 1.2 Hebbian Learning — `memosyne slumber --hebbian`

Memories that are searched together get their Tapestry `co_recalled` edge
strengthened. Pure structural — no LLM.

- **When to run**: weekly. It's cheap.
- **What it does**: future searches that hit one will rank the other
  higher via the graph walk.
- **Side effect**: makes the Tapestry less neutral over time. Good for
  personalization, bad if you want a pristine recall baseline.

### 1.3 The Lethe Protocol — `memosyne slumber --forget --dry-run`

Marks long-dormant memories as `dormant: true` (not deleted). They drop
out of default search but `aletheia_revert` can restore.

- **Always dry-run first.** Review the candidate list with the user.
- **Threshold**: ACT-R activation below a cutoff for > N days.
- **What never gets forgotten**: `10_Profile/`, `40_Projects/`, anything
  tagged `evergreen`. Same prefixes that have `half_life_days = 0` in
  Chronicle config.

---

## 2. Combined run

```
memosyne slumber              # all three rituals, sane defaults
memosyne slumber --stats      # what would change, no writes
```

When in doubt, `--stats` first so you can describe to the user what's
about to shift.

---

## 3. After-slumber verification

- **Reflection**: open the new `reflections/YYYY-MM-DD.md` — does it
  resonate? If not, the LLM may have over-generalized; adjust `--days`.
- **Hebbian**: `memosyne tapestry --stats` — `co_recalled` edge count
  should rise.
- **Lethe**: `memosyne search` for a known-dormant memory — should not
  appear in default top-k, but `--include-dormant` should still find it.

---

## 4. Cadence guidance

| Cadence | What to run |
|---|---|
| After every ingest | nothing — slumber is not per-ingest |
| Weekly | `--reflect --days 7 --hebbian` |
| Monthly | `--reflect --days 30 --forget --dry-run` |
| Quarterly | review `reflections/` directory with the user |

Don't automate `--forget --apply`. Forgetting should always involve the
user reviewing the candidate list — it's their autobiography.
