# Memosyne — top-level developer entry points.
#
# Most engineering happens through `memosyne` (the CLI). The Makefile
# only wraps a few cross-cutting workflows that don't fit a single
# subcommand: verification, sample bring-up, packaging hygiene.
#
# Verification policy: `make verify` must be green before tagging a
# release. CI runs the same target so local + remote stay aligned.

.PHONY: help verify verify-staged sample-rebuild sample-eval mcp-smoke clean-build

help:
	@echo "Memosyne — make targets"
	@echo ""
	@echo "  verify           Run all invariants + unit tests"
	@echo "  verify-staged    Same, but only inspect staged diff for path checks"
	@echo "  sample-rebuild   Rebuild retrieval indexes against sample_vault/"
	@echo "  sample-eval      Run memosyne eval --sample"
	@echo "  mcp-smoke        Run offline MCP stdio smoke test"
	@echo "  clean-build      Remove egg-info / dist / build artifacts"

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
