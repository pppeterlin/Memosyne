# Memosyne — developer entry points.
#
# Day-to-day use goes through the `memosyne` CLI. The Makefile only wraps
# cross-cutting workflows: verification, the sample vault, evaluation, release.

PYTHON ?= python3
SAMPLE_ENV = MEMOSYNE_VAULT_DIR=$(CURDIR)/sample_vault MEMOSYNE_ARTIFACT_DIR=$(CURDIR)/sample_vault/_artifacts

.PHONY: help verify verify-staged test sample-rebuild sample-eval mcp-smoke \
        eval eval-full eval-ci clean-build release release-dry release-notes

help:
	@echo "Memosyne — make targets"
	@echo ""
	@echo "  verify           Static invariants + unit tests (core install is enough)"
	@echo "  verify-staged    Same, but path checks only inspect the staged diff"
	@echo "  test             Unit tests only"
	@echo "  sample-rebuild   Rebuild every index for sample_vault/"
	@echo "  sample-eval      Golden-set evaluation against sample_vault/"
	@echo "  mcp-smoke        Offline MCP stdio smoke test against sample_vault/"
	@echo ""
	@echo "Evaluation on your own vault (needs the search extra):"
	@echo "  eval             Default search path (core profile), N=500, hygiene"
	@echo "  eval-full        Full labs stack, same sample — compare with eval"
	@echo "  eval-ci          eval + exit 1 on regression against the last report"
	@echo ""
	@echo "Release:"
	@echo "  release VERSION=1.5.0       Verify, tag, draft notes"
	@echo "  release-dry VERSION=1.5.0   Show what release would do"
	@echo "  clean-build                 Remove build artifacts"

verify:
	@PYTHON=$(PYTHON) bash scripts/verify.sh

verify-staged:
	@PYTHON=$(PYTHON) bash scripts/verify.sh --staged

test:
	@$(PYTHON) -m pytest tests/unit -q -p no:cacheprovider

sample-rebuild:
	@$(SAMPLE_ENV) $(PYTHON) -m memosyne rebuild --full

sample-eval:
	@$(PYTHON) -m memosyne eval --sample

mcp-smoke:
	@$(PYTHON) tests/test_mcp_smoke.py

eval:
	@$(PYTHON) -m memosyne.labs.retrieval_eval --config core --n 500 --hygiene --diff

eval-full:
	@$(PYTHON) -m memosyne.labs.retrieval_eval --config full --n 500 --hygiene --diff

eval-ci:
	@$(PYTHON) -m memosyne.labs.retrieval_eval --config core --n 500 --hygiene --diff --fail-on-regression

clean-build:
	@$(PYTHON) -c "import shutil, glob; [shutil.rmtree(p, ignore_errors=True) for p in ['build', 'dist', *glob.glob('*.egg-info'), *glob.glob('src/*.egg-info')]]"

release:
	@if [ -z "$(VERSION)" ]; then echo "✗ usage: make release VERSION=X.Y.Z"; exit 2; fi
	@bash scripts/release.sh $(VERSION)

release-dry:
	@if [ -z "$(VERSION)" ]; then echo "✗ usage: make release-dry VERSION=X.Y.Z"; exit 2; fi
	@bash scripts/release.sh $(VERSION) --dry-run

# Regenerate just the notes draft for a tag that already exists.
release-notes:
	@if [ -z "$(VERSION)" ]; then echo "✗ usage: make release-notes VERSION=X.Y.Z"; exit 2; fi
	@prev=$$(git describe --tags --abbrev=0 v$(VERSION)^ 2>/dev/null || echo ""); \
	  out=/tmp/release_$(VERSION).md; \
	  { \
	    echo "## Summary"; echo ""; echo "<one-paragraph pitch>"; echo ""; \
	    echo "## Commits since $$prev"; echo ""; \
	    if [ -n "$$prev" ]; then \
	      git log --pretty=format:"- %s (%h)" $$prev..v$(VERSION) | grep -v "^- Merge "; \
	    else \
	      git log --pretty=format:"- %s (%h)" v$(VERSION) | grep -v "^- Merge "; \
	    fi; \
	    echo ""; echo ""; \
	  } > $$out; \
	  echo "✓ regenerated $$out"
