#!/usr/bin/env python3
"""
Run a golden-set retrieval evaluation against the currently configured vault.

This is a thin driver around `benchmark/retrieval_eval.py`'s golden-set
helpers. It avoids the larger HyQE/Eternal-Mirror sampling path so it can
be invoked from `memosyne eval` for quick verification on small sets such
as `sample_vault/_eval/golden.yaml`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SYSTEM_DIR))
sys.path.insert(0, str(SYSTEM_DIR / "benchmark"))

from retrieval_eval import (  # noqa: E402
    CONFIGS,
    evaluate_golden,
    load_golden_set,
)


def _print_summary(metrics: dict, golden_path: Path, config_name: str, top_k: int) -> None:
    print(f"📿 Golden set : {golden_path}")
    print(f"   Config     : {config_name}   top_k={top_k}")
    print(f"   Questions  : {metrics['n']}")
    print()
    print(f"   Recall@1   : {metrics['recall@1']:.3f}")
    print(f"   Recall@5   : {metrics['recall@5']:.3f}")
    print(f"   Recall@10  : {metrics['recall@10']:.3f}")
    print(f"   MRR        : {metrics['mrr']:.3f}")
    print()
    print("   Per-question:")
    for row in metrics["per_question"]:
        rank = row["rank"]
        mark = "✅" if rank == 1 else ("◽" if rank else "❌")
        rank_str = f"@{rank}" if rank else "miss"
        q = row["question"]
        if len(q) > 60:
            q = q[:57] + "…"
        print(f"     {mark} {rank_str:<6}  {q}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--golden", required=True, help="path to a golden_set.yaml")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--config", default="baseline", choices=list(CONFIGS.keys()))
    args = ap.parse_args(argv)

    golden_path = Path(args.golden).expanduser()
    if not golden_path.is_absolute():
        golden_path = (Path.cwd() / golden_path).resolve()
    if not golden_path.exists():
        print(f"[fail] golden set not found: {golden_path}")
        return 1

    samples = load_golden_set(golden_path)
    if not samples:
        print(f"[fail] golden set empty or invalid: {golden_path}")
        return 1

    config = CONFIGS[args.config]
    metrics = evaluate_golden(samples, config, args.top_k)
    _print_summary(metrics, golden_path, args.config, args.top_k)

    if metrics["n"] == 0 or metrics["recall@10"] == 0.0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
