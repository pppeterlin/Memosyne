# _archive/

Historical planning documents from earlier project phases. **Not the current
plan** — kept for context, not for guidance.

The active planning surface is now:

- `ROADMAP.md` (top level) — release-level plan
- `docs/v0.X_*.md` — per-release planning docs (v0.2 onward)
- `CHANGELOG.md` — release notes

## What's in here

| File | Era | Why archived |
|------|-----|--------------|
| `TODO_retrieval_v2.md` | v0.2 (2026-04) | Detailed task tracker for the v0.2 retrieval freeze. Most items done; the same scope is now reflected in `docs/v0.2_retrieval_freeze.md` + actual code. |
| `優化方案_索引與保存管理.md` | v0.2 (2026-04) | Design memo proposing the v2 retrieval upgrade. Every shipped piece lives in real code now; deferred pieces have been folded into v0.3–v0.7 plans or retired. |
| `專案藍圖_V2.md` | Pre-v0.1 | Original Gemini-suggested blueprint when the project was just an idea. The architecture has evolved several generations past this. |

## Why archive instead of delete

- Old commits reference these files; broken links in git history are noise.
- Pre-v0.5 design choices are easier to understand with the original
  documents in hand (e.g. why Tapestry uses Kuzu, why ACT-R alpha is 0.2).
- README acknowledgements still reference the v2 memo as the design lineage.

## Rule of thumb

If you're reading source code and want to understand **why** something
exists the way it does, these files may help. If you're trying to figure
out **what to build next**, look at `docs/v0.7_*.md` / `ROADMAP.md` instead.
