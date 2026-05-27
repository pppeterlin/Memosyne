# Memosyne — top-level developer entry points.
#
# Most engineering happens through `memosyne` (the CLI). The Makefile
# only wraps a few cross-cutting workflows that don't fit a single
# subcommand: verification, sample bring-up, packaging hygiene.
#
# Verification policy: `make verify` must be green before tagging a
# release. CI runs the same target so local + remote stay aligned.

.PHONY: help verify verify-staged sample-rebuild sample-eval mcp-smoke clean-build release release-dry release-notes

help:
	@echo "Memosyne — make targets"
	@echo ""
	@echo "  verify           Run all invariants + unit tests"
	@echo "  verify-staged    Same, but only inspect staged diff for path checks"
	@echo "  sample-rebuild   Rebuild retrieval indexes against sample_vault/"
	@echo "  sample-eval      Run memosyne eval --sample"
	@echo "  mcp-smoke        Run offline MCP stdio smoke test"
	@echo "  clean-build      Remove egg-info / dist / build artifacts"
	@echo ""
	@echo "Release:"
	@echo "  release VERSION=0.7.0       Bump pyproject + commit + tag (preflight gated)"
	@echo "  release-dry VERSION=0.7.0   Dry-run; show what release would do"
	@echo "  release-notes VERSION=0.7.0 Regenerate the draft notes only"

verify:
	@bash scripts/verify.sh

verify-staged:
	@bash scripts/verify.sh --staged

sample-rebuild:
	@MEMOSYNE_VAULT_DIR=sample_vault python memosyne.py rebuild --rebuild

sample-eval:
	@python memosyne.py eval --sample

mcp-smoke:
	@python tests/test_mcp_smoke.py

clean-build:
	@rm -rf build/ dist/ *.egg-info

release:
	@if [ -z "$(VERSION)" ]; then echo "✗ usage: make release VERSION=X.Y.Z"; exit 2; fi
	@bash scripts/release.sh $(VERSION)

release-dry:
	@if [ -z "$(VERSION)" ]; then echo "✗ usage: make release-dry VERSION=X.Y.Z"; exit 2; fi
	@bash scripts/release.sh $(VERSION) --dry-run

# Regenerate just the notes draft for a tag that already exists (or HEAD).
# Useful if you want to iterate on release notes without re-tagging.
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
