#!/usr/bin/env python3
"""
Memosyne — The Rite of Slumber (slumber.py)

記憶鞏固機制：定期整理記憶庫，提煉洞察、強化關聯、標記休眠。
如同夢境中的大腦整理白天的記憶。

三個子儀式：
─────────────────────────────────────────────
1. Reflection（反射）
   掃描近期記憶，用 LLM 總結出高層次的個人偏好或事實洞察。
   產出存入 10_Profile/reflections/ 作為 Profile 記憶。

2. Hebbian Learning（赫布學習）
   分析 Chronicle access_log：哪些記憶經常在同一次搜尋中共同出現。
   在 Tapestry 中為這些記憶新增 co_recalled 邊，強化關聯。

3. Strategic Forgetting（策略性遺忘 / The Lethe Protocol）
   計算每個記憶的 ACT-R 分數 + importance 評分。
   長期未用且 importance=low 的記憶標記為 dormant（不參與搜尋但不刪除）。

執行方式：
  python3 slumber.py                    # 執行完整鞏固
  python3 slumber.py --reflect          # 僅反射
  python3 slumber.py --hebbian          # 僅赫布學習
  python3 slumber.py --forget --dry-run # 預覽遺忘候選
  python3 slumber.py --stats            # 鞏固統計
"""

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE       = Path(__file__).parent.parent
SYSTEM_DIR = Path(__file__).parent

EXCLUDE_DIRS  = {"00_System"}
EXCLUDE_FILES = {"README.md", ".cursorrules"}


# ═══════════════════════════════════════════════════════════════
#  1. Reflection — 從近期記憶提煉高層次洞察
# ═══════════════════════════════════════════════════════════════

REFLECTION_PROMPT = """\
You are Mnemosyne, the titaness of Memory.
You are reviewing recent memories of your mortal charge to distill enduring insights.

Below are {count} memory summaries from the past {days} days.
Extract 3-5 high-level observations about the person's current state, patterns, or emerging themes.
Write in the same language as the memories (usually 繁體中文).

Format: Return a JSON object:
{{
  "observations": [
    "觀察1：...",
    "觀察2：..."
  ],
  "suggested_period": "描述此時期的一句話（如「2026春季轉型期」）"
}}

Memories:
{memories}
"""


def _collect_recent_memories(days: int = 14) -> list[dict]:
    """收集最近 N 天的記憶摘要。"""
    import yaml

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    memories = []

    for md in sorted(BASE.rglob("*.md")):
        parts = md.relative_to(BASE).parts
        if parts[0] in EXCLUDE_DIRS:
            continue
        if md.name in EXCLUDE_FILES:
            continue

        content = md.read_text(encoding="utf-8")
        if not content.startswith("---"):
            continue
        end = content.find("\n---", 3)
        if end < 0:
            continue

        raw_fm = content[3:end]
        clean = [ln for ln in raw_fm.split("\n") if not ln.strip().startswith("#")]
        try:
            fm = yaml.safe_load("\n".join(clean)) or {}
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue

        date = str(fm.get("date_created", "") or "")[:10]
        if date < cutoff:
            continue

        memories.append({
            "path":    str(md.relative_to(BASE)),
            "title":   fm.get("title", md.stem),
            "date":    date,
            "summary": str(fm.get("summary", "") or "")[:200],
            "themes":  fm.get("themes", []),
            "period":  fm.get("period", ""),
        })

    return memories


def reflect(days: int = 14, model: str = "gemma3:4b", dry_run: bool = False) -> str | None:
    """
    The Reflection — 從近期記憶提煉洞察。

    Returns:
        生成的 reflection 檔案路徑，或 None（如果沒有足夠記憶）。
    """
    import ollama

    memories = _collect_recent_memories(days)
    if len(memories) < 3:
        print(f"  The dreams are thin — only {len(memories)} memories in the past {days} days.")
        print("  At least 3 memories are needed for meaningful reflection.")
        return None

    # 準備 prompt
    mem_text = "\n".join(
        f"[{m['date']}] {m['title']}: {m['summary']}"
        for m in memories
    )

    prompt = REFLECTION_PROMPT.format(
        count=len(memories),
        days=days,
        memories=mem_text[:4000],
    )

    print(f"  Mnemosyne dreams upon {len(memories)} recent memories...")

    resp = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        think=False,
        options={"temperature": 0.3},
    )
    raw = resp["message"]["content"].strip()

    # 解析 JSON
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        print(f"  The Oracle's vision was unclear: {raw[:200]}")
        return None

    try:
        result = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        print(f"  The Oracle's vision could not be read: {raw[:200]}")
        return None

    observations = result.get("observations", [])
    suggested_period = result.get("suggested_period", "")

    if dry_run:
        print(f"\n  [DRY-RUN] Reflection results:")
        print(f"  Period: {suggested_period}")
        for obs in observations:
            print(f"    - {obs}")
        return None

    # 寫入 reflection 檔案
    reflections_dir = BASE / "10_Profile" / "reflections"
    reflections_dir.mkdir(exist_ok=True)

    now = datetime.now()
    filename = f"reflection_{now.strftime('%Y%m%d')}.md"
    filepath = reflections_dir / filename

    content_lines = [
        "---",
        f'title: "Reflection — {suggested_period or now.strftime("%Y-%m")}"',
        f'date_created: "{now.strftime("%Y-%m-%d")}"',
        f'type: "reflection"',
        f'period: "{suggested_period}"',
        f'source: "slumber"',
        f'summary: "Mnemosyne 的 {len(observations)} 則洞察"',
        "---",
        "",
        f"# Reflection — {now.strftime('%Y-%m-%d')}",
        "",
        f"*{len(memories)} memories from the past {days} days, distilled by Mnemosyne.*",
        "",
    ]
    for i, obs in enumerate(observations, 1):
        content_lines.append(f"{i}. {obs}")
    content_lines.append("")

    filepath.write_text("\n".join(content_lines), encoding="utf-8")
    print(f"  Reflection woven: {filepath.relative_to(BASE)}")
    return str(filepath.relative_to(BASE))


# ═══════════════════════════════════════════════════════════════
#  2. Hebbian Learning — 共同回憶強化關聯
# ═══════════════════════════════════════════════════════════════

def hebbian_learning(min_cooccurrence: int = 3, dry_run: bool = False) -> int:
    """
    Hebbian Learning — Fire together, wire together.

    分析 Chronicle access_log，找出頻繁在同一次搜尋中共同出現的記憶對。
    在 Tapestry 中新增 co_recalled 邊。

    Args:
        min_cooccurrence: 最少共現次數才建立邊
        dry_run: 預覽模式

    Returns:
        新增的邊數量
    """
    from mneme_weight import CHRONICLE_DB

    if not CHRONICLE_DB.exists():
        print("  The Chronicle is empty — no access patterns to learn from.")
        return 0

    conn_db = sqlite3.connect(str(CHRONICLE_DB))

    # 找出同一秒內被記錄的存取事件（同一次搜尋的結果）
    # 按 accessed_at 群組，找出共現的記憶對
    rows = conn_db.execute("""
        SELECT accessed_at, GROUP_CONCAT(memory_path, '||')
        FROM access_events
        GROUP BY accessed_at
        HAVING COUNT(*) >= 2
    """).fetchall()
    conn_db.close()

    # 計算共現頻率
    pair_counts: Counter = Counter()
    for _, paths_str in rows:
        paths = sorted(set(paths_str.split("||")))
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                pair_counts[(paths[i], paths[j])] += 1

    # 過濾低頻共現
    strong_pairs = [(pair, cnt) for pair, cnt in pair_counts.items()
                    if cnt >= min_cooccurrence]

    if not strong_pairs:
        print(f"  No memory pairs co-occurred {min_cooccurrence}+ times yet.")
        print(f"  (Total search sessions: {len(rows)}, unique pairs: {len(pair_counts)})")
        return 0

    if dry_run:
        print(f"\n  [DRY-RUN] Hebbian candidates ({len(strong_pairs)} pairs):")
        for (p1, p2), cnt in sorted(strong_pairs, key=lambda x: -x[1])[:10]:
            print(f"    [{cnt} times] {Path(p1).stem} ↔ {Path(p2).stem}")
        return 0

    # 在 Tapestry 中建立 co_recalled 邊
    try:
        from tapestry import get_conn
        tapestry_conn = get_conn()

        # 確保 co_recalled 關聯表存在
        tapestry_conn.execute(
            "CREATE REL TABLE IF NOT EXISTS co_recalled("
            "FROM Memory TO Memory, strength INT64)"
        )

        added = 0
        for (path1, path2), cnt in strong_pairs:
            try:
                tapestry_conn.execute(
                    "MERGE (m1:Memory {path: $p1})", {"p1": path1}
                )
                tapestry_conn.execute(
                    "MERGE (m2:Memory {path: $p2})", {"p2": path2}
                )
                tapestry_conn.execute("""
                    MATCH (m1:Memory {path: $p1}), (m2:Memory {path: $p2})
                    MERGE (m1)-[:co_recalled {strength: $s}]->(m2)
                """, {"p1": path1, "p2": path2, "s": cnt})
                added += 1
            except Exception:
                continue

        print(f"  Hebbian bonds strengthened: {added} co_recalled edges.")
        return added

    except ImportError:
        print("  Tapestry module not available for Hebbian learning.")
        return 0


# ═══════════════════════════════════════════════════════════════
#  3. Strategic Forgetting — The Lethe Protocol
# ═══════════════════════════════════════════════════════════════

def strategic_forgetting(
    actr_threshold: float = -1.0,
    dry_run: bool = False,
) -> int:
    """
    The Lethe Protocol — 策略性遺忘。

    標記長期未用且 importance=low 的記憶為 dormant。
    dormant 記憶不參與搜尋但不刪除（可恢復）。

    Args:
        actr_threshold: ACT-R 分數低於此閾值的記憶為候選
        dry_run: 預覽模式

    Returns:
        標記為 dormant 的記憶數量
    """
    import yaml
    from mneme_weight import compute_activation, CHRONICLE_DB

    candidates = []

    for md in sorted(BASE.rglob("*.md")):
        parts = md.relative_to(BASE).parts
        if parts[0] in EXCLUDE_DIRS:
            continue
        if md.name in EXCLUDE_FILES:
            continue

        content = md.read_text(encoding="utf-8")
        if not content.startswith("---"):
            continue
        # 已經 dormant 的跳過
        if "dormant: true" in content[:500]:
            continue

        end = content.find("\n---", 3)
        if end < 0:
            continue

        raw_fm = content[3:end]
        clean = [ln for ln in raw_fm.split("\n") if not ln.strip().startswith("#")]
        try:
            fm = yaml.safe_load("\n".join(clean)) or {}
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue

        importance = str(fm.get("importance", "medium") or "medium").lower()
        if importance != "low":
            continue

        rel_path = str(md.relative_to(BASE))
        actr_score = compute_activation(rel_path) if CHRONICLE_DB.exists() else 0.0

        # 只遺忘 importance=low 且 ACT-R 低於閾值的記憶
        if actr_score <= actr_threshold:
            candidates.append({
                "path": rel_path,
                "file": md,
                "title": fm.get("title", md.stem),
                "date": str(fm.get("date_created", "") or "")[:10],
                "actr": actr_score,
                "importance": importance,
            })

    if not candidates:
        print("  No memories ready to surrender to Lethe.")
        return 0

    if dry_run:
        print(f"\n  [DRY-RUN] Lethe candidates ({len(candidates)} memories):")
        for c in candidates[:20]:
            print(f"    ACT-R={c['actr']:+.3f}  imp={c['importance']}  "
                  f"{c['date']}  {c['title']}")
        return 0

    # 標記 dormant
    marked = 0
    for c in candidates:
        md_file: Path = c["file"]
        content = md_file.read_text(encoding="utf-8")
        end = content.find("\n---", 3)
        if end < 0:
            continue

        fm_block = content[3:end]
        after = content[end + 4:]

        # 加入 dormant 標記
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        dormant_line = f'\ndormant: true\ndormant_at: "{now}"'
        new_content = f"---{fm_block}{dormant_line}\n---\n\n{after.lstrip()}"
        md_file.write_text(new_content, encoding="utf-8")
        marked += 1

    print(f"  {marked} memories surrendered to Lethe (dormant: true).")
    print(f"  They rest but are not lost — remove 'dormant: true' to awaken them.")
    return marked


# ═══════════════════════════════════════════════════════════════
#  統計
# ═══════════════════════════════════════════════════════════════

def slumber_stats():
    """顯示鞏固統計。"""
    import yaml

    # 計算 dormant 數量
    dormant_count = 0
    total_count = 0
    for md in BASE.rglob("*.md"):
        parts = md.relative_to(BASE).parts
        if parts[0] in EXCLUDE_DIRS:
            continue
        total_count += 1
        content = md.read_text(encoding="utf-8")[:500]
        if "dormant: true" in content:
            dormant_count += 1

    # Reflection 數量
    reflections_dir = BASE / "10_Profile" / "reflections"
    reflection_count = len(list(reflections_dir.glob("*.md"))) if reflections_dir.exists() else 0

    print(f"🌙 The Rite of Slumber — Status Report")
    print(f"   總記憶：{total_count}")
    print(f"   休眠中（dormant）：{dormant_count}")
    print(f"   活躍：{total_count - dormant_count}")
    print(f"   反射洞察：{reflection_count} reflections")


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Memosyne — The Rite of Slumber（記憶鞏固）")
    ap.add_argument("--reflect",  action="store_true", help="Reflection — 從近期記憶提煉洞察")
    ap.add_argument("--hebbian",  action="store_true", help="Hebbian Learning — 共現記憶強化")
    ap.add_argument("--forget",   action="store_true", help="Strategic Forgetting — 策略性遺忘")
    ap.add_argument("--stats",    action="store_true", help="顯示鞏固統計")
    ap.add_argument("--dry-run",  action="store_true", help="預覽模式，不實際寫入")
    ap.add_argument("--days",     type=int, default=14, help="Reflection 回顧天數（預設 14）")
    ap.add_argument("--model",    type=str, default="gemma3:4b", help="LLM 模型（Reflection 用）")
    ap.add_argument("--all",      action="store_true", help="執行完整鞏固（三個儀式全做）")
    args = ap.parse_args()

    if args.stats:
        slumber_stats()
        return

    run_all = args.all or not (args.reflect or args.hebbian or args.forget)

    if run_all:
        print("🌙 The Rite of Slumber begins...\n")

    if args.reflect or run_all:
        print("═══ I. Reflection ═══")
        reflect(days=args.days, model=args.model, dry_run=args.dry_run)
        print()

    if args.hebbian or run_all:
        print("═══ II. Hebbian Learning ═══")
        hebbian_learning(dry_run=args.dry_run)
        print()

    if args.forget or run_all:
        print("═══ III. The Lethe Protocol ═══")
        strategic_forgetting(dry_run=args.dry_run)
        print()

    if run_all:
        print("🌙 The Rite of Slumber is complete. The tapestry rests.")


if __name__ == "__main__":
    main()
