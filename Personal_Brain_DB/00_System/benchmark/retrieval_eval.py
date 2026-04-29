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
if not REPORTS_DIR.exists():
    REPORTS_DIR.mkdir(parents=True)


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
    # No muse routing — 純 hybrid RRF（參考基準）
    "baseline": dict(auto_route=False, muse_mode="soft", return_parent=False, muse_boost_k=0.0),
    # 當前預設：auto_route + soft penalty（非命中按 router 信心扣 up to 15%）
    "full":     dict(auto_route=True,  muse_mode="penalty", return_parent=True,
                     auto_route_threshold=0.20, muse_penalty_k=0.5, muse_penalty_min=0.85),
    # 歷史對照：confidence-scaled boost（v0.2-rc1 舊行為）
    "boost":    dict(auto_route=True,  muse_mode="soft", return_parent=True,
                     auto_route_threshold=0.20, muse_boost_k=2.0, muse_boost_max=1.5),
    # 歷史對照：flat boost（v0.1 舊行為）
    "flat":     dict(auto_route=True,  muse_mode="soft", return_parent=True,
                     muse_boost=1.30, auto_route_threshold=0.20, muse_boost_k=0.0),
    # Hard filter：非命中繆思直接剔除
    "hard":     dict(auto_route=True,  muse_mode="hard", return_parent=True, muse_boost_k=0.0),
}


# ── 分層（Phase 5.5）──────────────────────────────────────
def length_bucket(text: str) -> str:
    """以問題長度為代理（無需 I/O）：short/medium/long"""
    n = len(text)
    if n < 15:
        return "short"
    if n < 40:
        return "medium"
    return "long"


def _stratum_key(item: dict, by: str) -> str:
    if by == "muse":
        return muse_of(item["path"])
    if by == "length":
        return length_bucket(item["question"])
    if by == "both":
        return f"{muse_of(item['path'])}/{length_bucket(item['question'])}"
    return "all"


# ── 抽樣 ──────────────────────────────────────────────────
def load_samples(n: int, seed: int, stratify_by: str = "none") -> list[dict]:
    """從 hyqe_cache 隨機抽 n 題。每個 chunk 抽 1 題（第一題）。

    stratify_by: "none" / "muse" / "length" / "both"
        none: 純隨機；其他模式依 stratum 比例分配（每層至少 1 題）。
    """
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

    if stratify_by == "none":
        return items[:n]

    # 分層：按 stratum 比例抽，加總達 n
    strata: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        strata[_stratum_key(it, stratify_by)].append(it)

    total_pop = sum(len(v) for v in strata.values())
    picked: list[dict] = []
    # 先按比例分配配額（四捨五入；每層至少 1）
    quotas: dict[str, int] = {}
    for k, pool in strata.items():
        q = max(1, round(n * len(pool) / total_pop))
        quotas[k] = min(q, len(pool))

    # 校正總和至 n（可能因捨入偏離）
    drift = n - sum(quotas.values())
    keys_desc = sorted(quotas.keys(), key=lambda k: -len(strata[k]))
    i = 0
    while drift != 0 and keys_desc:
        k = keys_desc[i % len(keys_desc)]
        if drift > 0 and quotas[k] < len(strata[k]):
            quotas[k] += 1; drift -= 1
        elif drift < 0 and quotas[k] > 1:
            quotas[k] -= 1; drift += 1
        i += 1
        if i > len(keys_desc) * 4:
            break

    for k, q in quotas.items():
        picked.extend(strata[k][:q])

    rng.shuffle(picked)
    return picked[:n]


# ── 評估 ──────────────────────────────────────────────────
def evaluate(samples: list[dict], config: dict, top_k: int) -> dict:
    hits_at = {1: 0, 5: 0, 10: 0}
    mrr_total = 0.0
    per_muse_stats   = defaultdict(lambda: {"total": 0, "hit@5": 0, "mrr": 0.0})
    per_length_stats = defaultdict(lambda: {"total": 0, "hit@5": 0, "mrr": 0.0})
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

        lb = length_bucket(q)
        per_length_stats[lb]["total"] += 1
        if rank and rank <= 5:
            per_length_stats[lb]["hit@5"] += 1
        per_length_stats[lb]["mrr"] += rr

        per_question.append({
            "question": q,
            "target": target,
            "muse": muse,
            "len_bucket": lb,
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
        "per_length": {
            lb: {
                "total": s["total"],
                "recall@5": s["hit@5"] / s["total"] if s["total"] else 0.0,
                "mrr":      s["mrr"]   / s["total"] if s["total"] else 0.0,
            }
            for lb, s in per_length_stats.items()
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
    ]
    # Phase 5.3: Eternal Mirror + Augury 雙欄對照（若兩者皆有）
    aug = metrics.get("augury")
    if aug:
        md += [
            "## 總體指標 — Eternal Mirror vs Augury（Phase 5.3）",
            "| 指標 | Eternal Mirror（自監督）| Augury（人工 golden_set）|",
            "|---|---:|---:|",
            f"| N | {metrics['n']} | {aug['n']} |",
            f"| Recall@1 | {metrics['recall@1']:.3f} | {aug['recall@1']:.3f} |",
            f"| Recall@5 | {metrics['recall@5']:.3f} | {aug['recall@5']:.3f} |",
            f"| Recall@10 | {metrics['recall@10']:.3f} | {aug['recall@10']:.3f} |",
            f"| MRR | {metrics['mrr']:.3f} | {aug['mrr']:.3f} |",
            "",
        ]
    else:
        md += [
            "## 總體指標",
            f"| Recall@1 | Recall@5 | Recall@10 | MRR |",
            f"|---:|---:|---:|---:|",
            f"| {metrics['recall@1']:.3f} | {metrics['recall@5']:.3f} | {metrics['recall@10']:.3f} | {metrics['mrr']:.3f} |",
            "",
        ]
    md += [
        "## 分繆思領域",
        "| 繆思 | N | Recall@5 | MRR |",
        "|---|---:|---:|---:|",
    ]
    for muse, s in sorted(metrics["per_muse"].items()):
        md.append(f"| {muse} | {s['total']} | {s['recall@5']:.3f} | {s['mrr']:.3f} |")

    if metrics.get("per_length"):
        md += [
            "",
            "## 分問題長度（Phase 5.5）",
            "| 長度 bucket | N | Recall@5 | MRR |",
            "|---|---:|---:|---:|",
        ]
        for lb in ("short", "medium", "long"):
            s = metrics["per_length"].get(lb)
            if not s: continue
            md.append(f"| {lb} | {s['total']} | {s['recall@5']:.3f} | {s['mrr']:.3f} |")

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


# ── 回歸閾值（Phase 5.2）──
# 任一指標跌幅超過下列比例即觸發警告/失敗，避免靜默退步。
REGRESSION_THRESHOLDS = {
    "recall@1":  0.02,
    "recall@5":  0.02,
    "recall@10": 0.02,
    "mrr":       0.02,
}


def print_diff(cur: Path, prev: Path, fail_on_regression: bool = False) -> int:
    """回傳退步指標數（>=1 且 fail_on_regression 時 exit non-zero）。"""
    c = json.loads(cur.read_text())["metrics"]
    p = json.loads(prev.read_text())["metrics"]
    print(f"\n📊 Diff vs {prev.name}")
    regressions = 0
    for k in ("recall@1", "recall@5", "recall@10", "mrr"):
        delta = c[k] - p[k]
        threshold = REGRESSION_THRESHOLDS.get(k, 0.02)
        if delta > 0.005:
            arrow = "▲"
        elif delta < -threshold:
            arrow = "🚨"
            regressions += 1
        elif delta < -0.005:
            arrow = "▼"
        else:
            arrow = "・"
        print(f"  {arrow} {k:<10}  {p[k]:.3f} → {c[k]:.3f}  ({delta:+.3f})")
    if regressions:
        print(f"\n⚠️  {regressions} 項指標超出 {int(list(REGRESSION_THRESHOLDS.values())[0]*100)}% 退步閾值")
    return regressions


# ── Augury golden_set 整合（Phase 5.3）─────────────────────
def load_golden_set(path: Path) -> list[dict]:
    """讀 golden_set.yaml → 轉成和 hyqe samples 相同 schema 的 list。

    每題會被展開：可能有多個 expected_paths，任一命中即算 hit。
    """
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items: list[dict] = []
    for muse, questions in data.items():
        if not isinstance(questions, list):
            continue
        for q in questions:
            if not isinstance(q, dict) or not q.get("query"):
                continue
            expected = q.get("expected_paths") or []
            if isinstance(expected, str):
                expected = [expected]
            items.append({
                "key": f"golden::{muse}::{q['query'][:30]}",
                "path": expected[0] if expected else "",
                "expected_paths": expected,
                "question": q["query"],
                "muse_hint": muse,
                "tags": q.get("tags", []),
            })
    return items


def evaluate_golden(samples: list[dict], config: dict, top_k: int) -> dict:
    """Augury 版 evaluate：允許多個 expected_paths，任一命中算 hit。"""
    hits_at = {1: 0, 5: 0, 10: 0}
    mrr_total = 0.0
    per_question = []
    for sample in samples:
        q = sample["question"]
        expected = set(sample.get("expected_paths") or [sample["path"]])
        try:
            results = search(q, top_k=top_k, record_access=False, **config)
        except Exception as e:
            print(f"  [augury] search failed on {q!r}: {e}")
            continue
        paths = [r.get("path", "") for r in results]
        rank = None
        for idx, p in enumerate(paths, 1):
            if p in expected:
                rank = idx; break
        rr = 1.0 / rank if rank else 0.0
        mrr_total += rr
        for k in hits_at:
            if rank and rank <= k:
                hits_at[k] += 1
        per_question.append({
            "question": q, "expected": list(expected),
            "muse_hint": sample.get("muse_hint"),
            "rank": rank, "top_paths": paths[:5],
        })
    n = len(samples)
    return {
        "n": n,
        "recall@1":  hits_at[1] / n if n else 0.0,
        "recall@5":  hits_at[5] / n if n else 0.0,
        "recall@10": hits_at[10] / n if n else 0.0,
        "mrr":       mrr_total / n if n else 0.0,
        "per_question": per_question,
    }


# ── main ──────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500, help="抽樣題數")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--config", default="baseline", choices=list(CONFIGS.keys()))
    ap.add_argument("--diff", action="store_true", help="與上一份同 config 報告比對")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="--diff 搭配：任一指標跌幅 >2% 時 exit 1（CI 用）")
    ap.add_argument("--hygiene", action="store_true",
                    help="Eval hygiene：排除 hyqe view，避免自我命中（資料洩漏）")
    ap.add_argument("--stratify-by", default="none",
                    choices=["none", "muse", "length", "both"],
                    help="分層抽樣（Phase 5.5）：按 muse/length 維度確保各層都有樣本")
    ap.add_argument("--golden-set", type=str, default="",
                    help="Phase 5.3：指定 golden_set.yaml 路徑，"
                         "將人工 Augury 指標並列到報告裡")
    args = ap.parse_args()

    config = dict(CONFIGS[args.config])
    if args.hygiene:
        config["exclude_views"] = ["hyqe"]
    strat_tag = f"_strat-{args.stratify_by}" if args.stratify_by != "none" else ""
    tag = f"{args.config}{'_hygiene' if args.hygiene else ''}{strat_tag}"
    print(f"🪞 Eternal Mirror · config={tag}  n={args.n}  top_k={args.top_k}  "
          f"seed={args.seed}  stratify={args.stratify_by}")
    samples = load_samples(args.n, args.seed, stratify_by=args.stratify_by)
    print(f"   loaded {len(samples)} samples from hyqe_cache.json")

    metrics = evaluate(samples, config, args.top_k)

    print(f"\n總體：Recall@1={metrics['recall@1']:.3f}  "
          f"Recall@5={metrics['recall@5']:.3f}  "
          f"Recall@10={metrics['recall@10']:.3f}  "
          f"MRR={metrics['mrr']:.3f}")

    # Phase 5.3: Augury golden_set 並列
    augury_metrics = None
    if args.golden_set:
        gs_path = Path(args.golden_set)
        if not gs_path.is_absolute():
            gs_path = Path(__file__).resolve().parent / args.golden_set
        if gs_path.exists():
            gs_samples = load_golden_set(gs_path)
            if gs_samples:
                print(f"\n📿 Augury（golden_set）— {len(gs_samples)} 題")
                augury_metrics = evaluate_golden(gs_samples, config, args.top_k)
                print(f"   Recall@1={augury_metrics['recall@1']:.3f}  "
                      f"Recall@5={augury_metrics['recall@5']:.3f}  "
                      f"Recall@10={augury_metrics['recall@10']:.3f}  "
                      f"MRR={augury_metrics['mrr']:.3f}")
            else:
                print(f"⚠️  golden_set 無有效題目：{gs_path}")
        else:
            print(f"⚠️  golden_set 檔案不存在：{gs_path}")

    meta = {
        "config_name": tag,
        "config":      config,
        "top_k":       args.top_k,
        "seed":        args.seed,
        "timestamp":   datetime.now().isoformat(),
    }
    if augury_metrics is not None:
        metrics["augury"] = augury_metrics
    out = write_report(metrics, meta)
    print(f"📝 報告：{out}")

    if args.diff:
        prev = latest_prev_report(tag, out)
        if prev:
            regressions = print_diff(out, prev, args.fail_on_regression)
            if args.fail_on_regression and regressions:
                sys.exit(1)
        else:
            print("（無上一份報告可比對）")


if __name__ == "__main__":
    main()
