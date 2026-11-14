# Development

## Repository layout

```
src/memosyne/           the package — see docs/architecture.md for the module map
  labs/                 optional / experimental features
  skills/               agent-facing skill docs shipped with the package
  templates/            Markdown templates
tests/
  conftest.py           points every test at an empty temporary home
  unit/                 offline unit tests (core install is enough)
  test_mcp_smoke.py     end-to-end MCP stdio test against sample_vault
  Dockerfile            clean-install verification image
sample_vault/           fully synthetic vault used by tests, demos and CI
spring/                 default drop zone (contents are never committed)
scripts/                invariant checks, verify, release
docs/                   user docs; docs/history/ holds per-release planning notes
```

Your own vault is never part of this repository.

## Setup

```bash
git clone https://github.com/pppeterlin/Memosyne && cd Memosyne
uv sync --extra dev                 # core + pytest
uv sync --extra dev --extra all     # everything
# or: pip install -e ".[dev]"  /  pip install -e ".[dev,all]"
```

## Verify

```bash
make verify        # static invariants + unit tests; must be green before a release
make test          # unit tests only
memosyne quickstart            # builds sample indexes, evaluates, searches, digests
make mcp-smoke                 # MCP stdio end to end (run quickstart first)
```

`make verify` and the quickstart both work on the core install — no torch,
no ChromaDB, no LLM. CI runs exactly these on every push and pull request.

To exercise a clean machine end to end:

```bash
docker build -f tests/Dockerfile --target core   -t memosyne-verify-core .
docker build -f tests/Dockerfile --target search -t memosyne-verify .
```

## Rules of the road

- **Layering.** Core modules must not import `memosyne.labs` or the search
  extra's packages at module level. Import lazily, inside the function that
  needs it, behind a feature switch. `tests/unit/test_search_core.py` checks
  that the core profile never calls a labs stage.
- **Paths.** Only `memosyne/paths.py` decides where things live. Do not
  compute paths from `__file__`; ask `paths` for the vault, an artifact or
  the spring.
- **Ground truth.** Enrichment records only what the source text literally
  says. Anything derived by an LLM that is *not* a literal extraction belongs
  in a clearly separate place (aggregates, reflections), never in a memory's
  own frontmatter.
- **Deterministic first.** If a feature can work without an LLM and without
  an index (digest, gap questions, link extraction), build it that way.
- **Naming.** File and function names say what the code does. Mythological
  names are for CLI verbs and user-facing messages.
- **Evidence before defaults.** A retrieval stage becomes a default only
  with an evaluation that shows it helps ([evaluation.md](evaluation.md)).

## Privacy invariants

Three scripts run in `make verify`:

| Script | Checks |
|---|---|
| `check_no_real_paths.sh` | no real home-directory paths in code, tests, scripts or docs |
| `check_no_private_names.sh` | none of *your* private terms appear in code, tests, scripts or docs |
| `check_sample_vault_synthetic.sh` | the sample vault contains no secrets, host paths or unlisted entities |

`check_no_private_names.sh` only works if you give it a block-list: create
`.private_terms` in the repo root (git-ignored) or `~/.memosyne/private_terms`,
one term per line — names of people, places and employers that must never
appear in a commit. Without that file the check passes silently.

Never commit anything generated from a real vault: indexes, caches, graph
exports, evaluation reports, captured queries. Deleting a file later does not
remove it from git history.

Sample-vault content stays fully synthetic; see [sample_vault.md](sample_vault.md)
for the persona constraints.

## Release

The version lives in `pyproject.toml`. Bump it (and add a CHANGELOG entry) in
the release pull request, then, on an up-to-date `master`:

```bash
make release-dry VERSION=X.Y.Z   # shows what would happen
make release VERSION=X.Y.Z       # make verify → annotated tag → draft notes
git push origin master vX.Y.Z
gh release create vX.Y.Z --title "Memosyne vX.Y.Z — <theme>" --notes-file <draft>
```
