# Development

How to set up Memosyne for hacking and how to verify a release before publishing.

---

## Repository Layout

```
memosyne.py                     # Public CLI entry point (subcommands wrap 00_System scripts)
pyproject.toml                  # Packaging metadata; `pip install -e .` exposes the `memosyne` script
docs/                           # Public documentation
sample_vault/                   # Fully synthetic vault used by tests and demos
  _eval/golden.yaml             # Tiny golden set for `memosyne eval --sample`
  _artifacts/                   # gitignored — derived indexes when running in sample mode
tests/
  test_mcp_smoke.py             # Offline MCP stdio end-to-end test
Personal_Brain_DB/
  00_System/                    # Implementation modules
    artifacts.py                # Artifact registry + data_root() resolver
    vectorize.py                # Dense + BM25 indexing and search
    enrich.py                   # Oracle of Mneme entity extraction
    tapestry.py                 # Knowledge graph (Kuzu)
    mneme_weight.py             # ACT-R access log / Chronicle of Mneme
    augury.py                   # Memory quality audit
    slumber.py                  # Memory consolidation rituals
    aletheia.py                 # Aletheia correction tools
    mcp_server.py               # MCP server (FastMCP)
    benchmark/                  # Eternal Mirror retrieval evaluation
    eval_golden.py              # Driver behind `memosyne eval`
  _vault/                       # Private content; tracked as a submodule pointing at a private repo
```

The implementation modules can be invoked directly (`python Personal_Brain_DB/00_System/<script>.py …`) but the `memosyne` CLI is the supported public surface.

---

## Local Setup

```bash
git clone <repo-url> memosyne
cd memosyne

# Create and activate a Python 3.10+ environment by your preferred method
python -m venv .venv && source .venv/bin/activate

pip install -e .
pip install -r Personal_Brain_DB/00_System/requirements.txt
```

Memosyne does not bundle a generative LLM. For workflows that do need one (enrichment, contextualization, local chat), point Memosyne at a local LLM endpoint of your choice — see [configuration.md](configuration.md).

---

## Verifying Changes

Public verification path uses `sample_vault/`. No private data and no LLM are required.

```bash
# Build sample indexes (safe to rerun; clobbers sample_vault/_artifacts/ only)
MEMOSYNE_VAULT_DIR=$PWD/sample_vault \
MEMOSYNE_ARTIFACT_DIR=$PWD/sample_vault/_artifacts \
python memosyne.py rebuild

# Health
MEMOSYNE_VAULT_DIR=$PWD/sample_vault \
MEMOSYNE_ARTIFACT_DIR=$PWD/sample_vault/_artifacts \
python memosyne.py health

# Retrieval evaluation (5 synthetic golden questions)
python memosyne.py eval --sample

# MCP stdio end-to-end smoke test
python tests/test_mcp_smoke.py
```

These three together exercise: vault discovery, indexing, dense + BM25 search, retrieval ranking, MCP server lifecycle, and tool dispatch. If any of them regress on a PR, the change has touched something user-visible and needs investigation.

---

## Privacy Discipline

Public commits must never contain real user content, secrets, or anything derived from a private vault. Refer to [privacy.md](privacy.md) for the data layering rules.

Practical guardrails:

- `.gitignore` covers `.env`, `_internal/`, and the standard derived-artifact paths.
- The private vault is a submodule (`Personal_Brain_DB/_vault → <private-repo>`); the public repo only records the submodule pointer.
- `sample_vault/_artifacts/` is ignored, so demo runs do not pollute the working tree.

When opening a PR that touches files outside `00_System/`, run `git diff --stat origin/master...HEAD` and re-read the changed files for accidental private content.

---

## Release Checklist

Before tagging a public release (or before publishing a v0.x branch upstream):

1. **Working tree is clean** for the publishable surface.
   ```bash
   git status
   git diff --stat origin/master...HEAD
   ```
2. **Rebuild and verify on sample data.** All three of `health`, `eval --sample`, and `tests/test_mcp_smoke.py` must pass.
3. **Run a secret scan over the full git history.** Memosyne uses [gitleaks](https://github.com/gitleaks/gitleaks).
   ```bash
   # History scan — this is the one that matters for OSS publication
   gitleaks detect --source . --redact

   # Working-tree scan — expect findings inside .env, _internal/, and the
   # private _vault submodule. Confirm each finding is in a path that is
   # gitignored or inside the private submodule before approving.
   gitleaks dir --redact .
   ```
   Any history finding outside of intentional sample fixtures must be triaged before release: rotate the credential, scrub history if necessary, and refresh the release branch.
4. **Update `docs/v0.X_*.md`** if the release scope changed since the planning doc was written.
5. **Tag and publish** via `gh release create vX.Y.Z`. Match the title and body style of prior releases.

---

## Contribution Notes

- Match the existing commit message style — short imperative subject under ~70 characters, body wrapped at ~72.
- Sample-vault content stays fully synthetic. See `docs/sample_vault.md` for the persona constraints.
- New artifacts that need to follow `MEMOSYNE_VAULT_DIR` / `MEMOSYNE_ARTIFACT_DIR` should register in `Personal_Brain_DB/00_System/artifacts.py` rather than introducing new ad-hoc paths.
- Provider-specific names (Ollama, OpenRouter, particular models, etc.) are examples in public docs, not requirements. Avoid hard-coding them in code defaults or documentation tables.
