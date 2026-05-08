# Memosyne Correction

Aletheia is Memosyne's correction layer for fixing wrong, stale, or missing memory facts without manually editing private vault files blindly.

The core rule: **dry-run first, apply only after reviewing the diff**.

## What Aletheia Can Change

| Operation | Purpose | Surface |
|---|---|---|
| `ADD_FACT` | Add one `personal_facts` entry | frontmatter |
| `UPDATE_FACT` | Replace one matching `personal_facts` entry | frontmatter |
| `INVALIDATE_FACT` | Remove one matching `personal_facts` entry | frontmatter |
| `CORRECT_TEXT` | Literal substring replacement in body text | body |
| `REVERT` | Reverse an earlier logged operation | frontmatter/body |

All applied operations write an append-only log entry to `aletheia_log.jsonl`. Applied operations also create a snapshot under `aletheia_backup/` when possible.

## CLI Usage

Show current personal facts:

```bash
memosyne correct --show 30_Journal/2026/sample_note.md
```

Add a fact, dry-run first:

```bash
memosyne correct --add 30_Journal/2026/sample_note.md \
  --fact "sample-person lives in Tokyo"
```

Apply after reviewing the diff:

```bash
memosyne correct --add 30_Journal/2026/sample_note.md \
  --fact "sample-person lives in Tokyo" \
  --apply
```

Update a fact:

```bash
memosyne correct --update 30_Journal/2026/sample_note.md \
  --old "lives in Osaka" \
  --new "lives in Tokyo"
```

Invalidate a fact:

```bash
memosyne correct --invalidate 30_Journal/2026/sample_note.md \
  --match "lives in Tokyo"
```

Correct body text:

```bash
memosyne correct --correct 30_Journal/2026/sample_note.md \
  --old "old exact phrase" \
  --new "new exact phrase"
```

High-risk body corrections need explicit confirmation when applying:

```bash
memosyne correct --correct 30_Journal/2026/sample_note.md \
  --old "long old phrase" \
  --new "long new phrase" \
  --apply \
  --confirm
```

Revert an operation:

```bash
memosyne correct --revert abc123def456
memosyne correct --revert abc123def456 --apply
```

## MCP Usage

MCP correction tools are dry-run by default:

| Tool | Apply flag |
|---|---|
| `aletheia_add_fact` | `apply=false` by default |
| `aletheia_update_fact` | `apply=false` by default |
| `aletheia_invalidate_fact` | `apply=false` by default |
| `aletheia_correct_text` | `apply=false` by default |
| `aletheia_revert` | `apply=false` by default |

Recommended MCP flow:

1. Call the correction tool with `apply=false`.
2. Inspect the returned diff and target path.
3. Ask for confirmation.
4. Call the same tool with `apply=true`.
5. Rebuild retrieval indexes if body text or searchable metadata changed.

## Rebuild After Corrections

Frontmatter-only fact changes sync to Tapestry when possible, but retrieval indexes may still need refresh depending on the changed text.

Body corrections mark the memory as pending re-embed. Rebuild after applying:

```bash
memosyne rebuild
memosyne health
```

Check Chronicle and retrieval health:

```bash
memosyne chronicle --stats
memosyne search "測試查詢"
```

## Safety Rules

- Use unique substrings for `--old` and `--match`; ambiguous matches fail.
- Do not use Aletheia for broad structural rewrites.
- Do not manually edit generated logs unless recovering from a broken local state.
- Keep real correction examples out of public docs and issues.
- Use synthetic paths and generic facts in public examples.

## Troubleshooting

### Memory Not Found

```text
記憶不存在：...
```

Use a path relative to `Personal_Brain_DB/` or the private vault root. Search first if unsure:

```bash
memosyne search "測試查詢"
```

### Multiple Matches

```text
多個事實 match ...
```

Provide a longer, unique substring.

### Index Seems Stale

Run:

```bash
memosyne rebuild
```

### Need to Undo

Find the `log_id` returned by the applied operation, then dry-run and apply revert:

```bash
memosyne correct --revert abc123def456
memosyne correct --revert abc123def456 --apply
```
