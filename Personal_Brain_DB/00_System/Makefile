# Memosyne · Retrieval Evaluation Makefile
# 使用：cd Personal_Brain_DB/00_System && make eval

.PHONY: eval eval-full eval-hygiene eval-compare help

help:
	@echo "Available targets:"
	@echo "  make eval           — baseline eval (N=500)"
	@echo "  make eval-full      — full config eval (auto_route + muse)"
	@echo "  make eval-hygiene   — full config with hyqe exclusion"
	@echo "  make eval-compare   — full + hygiene + diff (CI mode)"
	@echo "  make eval-ci        — 同 eval-compare，退步 >2% 時 exit 1"

eval:
	python benchmark/retrieval_eval.py --config baseline --n 500 --diff

eval-full:
	python benchmark/retrieval_eval.py --config full --n 500 --diff

eval-hygiene:
	python benchmark/retrieval_eval.py --config full --n 500 --hygiene --diff

eval-compare:
	python benchmark/retrieval_eval.py --config full --n 500 --diff
	python benchmark/retrieval_eval.py --config full --n 500 --hygiene --diff

eval-ci:
	python benchmark/retrieval_eval.py --config full --n 500 --hygiene \
		--diff --fail-on-regression
