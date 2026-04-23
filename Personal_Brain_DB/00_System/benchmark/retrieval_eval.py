#!/usr/bin/env python3
"""
The Eternal Mirror — 自監督檢索評估（HyQE Round-trip）

以 hyqe_cache.json 中 LLM 生成的假設問題為 query，
回頭檢查 search() 能否在 top-K 命中 source chunk 所屬文件。

零人工標註：答案映射本來就綁在 (question → source path) 這對 tuple 裡。

指標：
  - Recall@1 / Recall@5 / Recall@10
  - MRR（Mean Reciprocal Rank）
  - 依 doc_type（繆思領域）分層報告

用法：
  python3 retrieval_eval.py                             # baseline config，抽 500 題
  python3 retrieval_eval.py --n 200 --top-k 10
  python3 retrieval_eval.py --config full               # auto_route + muse_boost + parent
  python3 retrieval_eval.py --seed 42                   # 可重現抽樣
  python3 retrieval_eval.py --diff                      # 與上一份報告比對
"""
import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict

SYSTEM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SYSTEM_DIR))

from vectorize import search  # noqa: E402

HYQE_CACHE = SYSTEM_DIR / "hyqe_cache.json"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ── 資料類型推斷（path → 繆思領域） ─────────────────────────
MUSE_BY_PREFIX = {
    "10_Profile": "Polyhymnia",
    "20_AI_Chats": "Calliope",
    "30_Journal": "Clio",
    "40_Projects": "Terpsichore",
    "50_Knowledge": "Urania",
}


def muse_of(path: str) -> str:
    for prefix, muse in MUSE_BY_PREFIX.items():
        if path.startswith(prefix):
            return muse
    return "Unknown"


# ── 設定 profiles ─────────────────────────────────────────
CONFIGS = {
    "baseline":     dict(auto_route=False, muse_mode="soft", return_parent=False),
    "full":         dict(auto_route=True,  muse_mode="soft", return_parent=True, muse_boost=1.30, auto_route_threshold=0.20),
    "full_light":   dict(auto_route=True,  muse_mode="soft", return_parent=True, muse_boost=1.15, auto_route_threshold=0.20),
    "full_strict":  dict(auto_route=True,  muse_mode="soft", return_parent=True, muse_boost=1.30, auto_route_threshold=0.30),
    "full_gentle":  dict(auto_route=True,  muse_mode="soft", return_parent=True, muse_boost=1.10, auto_route_threshold=0.30),
    "hard":         dict(auto_route=True,  muse_mode="hard", return_parent=True),
}


# ── 抽樣 ──────────────────────────────────────────────────
def load_samples(n: int, seed: int) -> list[dict]:
    """從 hyqe_cache 隨機抽 n 題。每個 chunk 抽 1 題（第一題）。"""
    cache = json.loads(HYQE_CACHE.read_text(encoding="utf-8"))
    items = []
    for key, qs in cache.items():
        if not qs:
            continue
        # key 格式：{rel_path}::para{i}
        path, _, _ = key.partition("::para")
        items.append({"key": key, "path": path, "question": qs[0]})

    rng = random.Random(seed)
    rng.shuffle(items)
    return items[:n]


# ── 評估 ──────────────────────────────────────────────────
def evaluate(samples: list[dict], config: dict, top_k: int) -> dict:
    hits_at = {1: 0, 5: 0, 10: 0}
    mrr_total = 0.0
    per_muse_stats = defaultdict(lambda: {"total": 0, "hit@5": 0, "mrr": 0.0})
    per_question = []
    t0 = time.time()

    for i, sample in enumerate(samples):
        q = sample["question"]
        target = sample["path"]
        muse = muse_of(target)
        try:
            results = search(q, top_k=top_k, record_access=False, **config)
        except Exception as e:
            print(f"  [eval] search failed on #{i}: {e}")
            continue

        paths = [r.get("path", "") for r in results]
        rank = None
        for idx, p in enumerate(paths, 1):
            if p == target:
                rank = idx
                break

        rr = 1.0 / rank if rank else 0.0
        mrr_total += rr
        for k in hits_at:
            if rank and rank <= k:
                hits_at[k] += 1

        per_muse_stats[muse]["total"] += 1
        if rank and rank <= 5:
            per_muse_stats[muse]["hit@5"] += 1
        per_muse_stats[muse]["mrr"] += rr

        per_question.append({
            "question": q,
            "target": target,
            "muse": muse,
            "rank": rank,
            "top_paths": paths[:5],
        })

        if (i + 1) % 50 == 0:
            print(f"  [eval] {i+1}/{len(samples)}  Recall@5={hits_at[5]/(i+1):.3f}  MRR={mrr_total/(i+1):.3f}")

    n = len(samples)
    elapsed = time.time() - t0
    return {
        "n": n,
        "recall@1":  hits_at[1] / n if n else 0.0,
        "recall@5":  hits_at[5] / n if n else 0.0,
        "recall@10": hits_at[10] / n if n else 0.0,
        "mrr":       mrr_total / n if n else 0.0,
        "per_muse": {
            m: {
                "total": s["total"],
                "recall@5": s["hit@5"] / s["total"] if s["total"] else 0.0,
                "mrr":      s["mrr"]   / s["total"] if s["total"] else 0.0,
            }
            for m, s in per_muse_stats.items()
        },
        "elapsed_sec": round(elapsed, 2),
        "per_question": per_question,
    }


# ── 報告 ──────────────────────────────────────────────────
def write_report(metrics: dict, meta: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    stem = f"eval_{meta['config_name']}_{ts}"
    json_path = REPORTS_DIR / f"{stem}.json"
    md_path   = REPORTS_DIR / f"{stem}.md"

    payload = {**meta, "metrics": {k: v for k, v in metrics.items() if k != "per_question"}}
    payload["per_question"] = metrics["per_question"]
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        f"# Eternal Mirror 評估報告 — {meta['config_name']}",
        f"- 時間：{ts}",
        f"- 樣本數：{metrics['n']}  | top_k={meta['top_k']}  | seed={meta['seed']}",
        f"- 耗時：{metrics['elapsed_sec']}s",
        "",
        "## 總體指標",
        f"| Recall@1 | Recall@5 | Recall@10 | MRR |",
        f"|---:|---:|---:|---:|",
        f"| {metrics['recall@1']:.3f} | {metrics['recall@5']:.3f} | {metrics['recall@10']:.3f} | {metrics['mrr']:.3f} |",
        "",
        "## 分繆思領域",
        "| 繆思 | N | Recall@5 | MRR |",
        "|---|---:|---:|---:|",
    ]
    for muse, s in sorted(metrics["per_muse"].items()):
        md.append(f"| {muse} | {s['total']} | {s['recall@5']:.3f} | {s['mrr']:.3f} |")

    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return json_path


def latest_prev_report(config_name: str, exclude: Path) -> Path | None:
    reps = sorted(
        REPORTS_DIR.glob(f"eval_{config_name}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for r in reps:
        if r != exclude:
            return r
    return None


def print_diff(cur: Path, prev: Path) -> None:
    c = json.loads(cur.read_text())["metrics"]
    p = json.loads(prev.read_text())["metrics"]
    print(f"\n📊 Diff vs {prev.name}")
    for k in ("recall@1", "recall@5", "recall@10", "mrr"):
        delta = c[k] - p[k]
        arrow = "▲" if delta > 0.005 else ("▼" if delta < -0.005 else "・")
        print(f"  {arrow} {k:<10}  {p[k]:.3f} → {c[k]:.3f}  ({delta:+.3f})")


# ── main ──────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500, help="抽樣題數")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--config", default="baseline", choices=list(CONFIGS.keys()))
    ap.add_argument("--diff", action="store_true", help="與上一份同 config 報告比對")
    args = ap.parse_args()

    config = CONFIGS[args.config]
    print(f"🪞 Eternal Mirror · config={args.config}  n={args.n}  top_k={args.top_k}  seed={args.seed}")
    samples = load_samples(args.n, args.seed)
    print(f"   loaded {len(samples)} samples from hyqe_cache.json")

    metrics = evaluate(samples, config, args.top_k)

    print(f"\n總體：Recall@1={metrics['recall@1']:.3f}  "
          f"Recall@5={metrics['recall@5']:.3f}  "
          f"Recall@10={metrics['recall@10']:.3f}  "
          f"MRR={metrics['mrr']:.3f}")

    meta = {
        "config_name": args.config,
        "config":      config,
        "top_k":       args.top_k,
        "seed":        args.seed,
        "timestamp":   datetime.now().isoformat(),
    }
    out = write_report(metrics, meta)
    print(f"📝 報告：{out}")

    if args.diff:
        prev = latest_prev_report(args.config, out)
        if prev:
            print_diff(out, prev)
        else:
            print("（無上一份報告可比對）")


if __name__ == "__main__":
    main()
