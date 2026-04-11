#!/usr/bin/env python3
"""
Memosyne — The Augury (augury.py)

記憶審計與修正機制。占卜記憶的健康，修正 Oracle 的過失。

三大儀式：
  I.   Inspect  — 透視一筆記憶的完整追溯鏈（YAML → Tapestry → 索引）
  II.  Correct  — 手動修正 enrichment，自動連鎖更新所有下游層
  III. Patrol   — 巡迴所有記憶，用 LLM 審計 enrichment 正確性

用法：
  python3 augury.py --inspect "workus"
  python3 augury.py --correct 30_Journal/2024/workus.md --remove-location "台灣" --add-location "深圳"
  python3 augury.py --patrol
  python3 augury.py --patrol --dry-run
  python3 augury.py --apply-report augury_report_20260411.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import logging
import warnings
from datetime import datetime
from pathlib import Path

logging.disable(logging.WARNING)
warnings.filterwarnings("ignore")

# TUN 模式 VPN 下，httpx 可能把 localhost 請求也送進 proxy → 502
os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")
for _var in ("NO_PROXY", "no_proxy"):
    _cur = os.environ.get(_var, "")
    _bypass = "localhost,127.0.0.1,::1"
    if _bypass not in _cur:
        os.environ[_var] = f"{_cur},{_bypass}".lstrip(",")

BASE       = Path(__file__).parent.parent
SYSTEM_DIR = Path(__file__).parent
REPORT_DIR = SYSTEM_DIR / "augury_reports"


# ═══════════════════════════════════════════════════════════════
#  I. INSPECT — 透視記憶追溯鏈
# ═══════════════════════════════════════════════════════════════

def find_memories(keyword: str) -> list[Path]:
    """
    用關鍵字搜尋記憶檔案。
    搜尋範圍（依序）：
      1. 檔名 / 路徑
      2. frontmatter title / filename_hint
      3. 檔案全文（body + enrichment）
    """
    EXCLUDE = {"00_System"}
    kw = keyword.lower()
    matches = []
    for md in sorted(BASE.rglob("*.md")):
        if any(part in EXCLUDE for part in md.relative_to(BASE).parts):
            continue
        if md.name in {"README.md", ".cursorrules"}:
            continue
        rel = str(md.relative_to(BASE))

        # ① 檔名 / 路徑
        if kw in rel.lower():
            matches.append(md)
            continue

        # ② 全文搜尋（讀檔，比對 title / filename_hint / body）
        try:
            content = md.read_text(encoding="utf-8")
        except Exception:
            continue
        if kw in content.lower():
            matches.append(md)

    return matches


def parse_enrichment_from_file(path: Path) -> dict | None:
    """從記憶檔案讀取 enrichment 欄位。"""
    import yaml

    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end < 0:
        return None
    raw_fm = content[3:end]
    clean = [ln for ln in raw_fm.split("\n") if not ln.strip().startswith("#")]
    try:
        fm = yaml.safe_load("\n".join(clean)) or {}
    except Exception:
        return None
    if not isinstance(fm, dict):
        return None
    return fm


def get_tapestry_edges(memory_path: str) -> list[dict]:
    """查詢 Tapestry 中與此記憶相關的所有邊。"""
    try:
        from tapestry import get_conn
    except ImportError:
        return []

    conn = get_conn()
    edges = []

    # 所有從此 Memory 出發的關係
    rel_queries = [
        ("mem_location", "Location", "name"),
        ("mem_person",   "Person",   "name"),
        ("mem_event",    "Event",    "name"),
        ("mem_period",   "Period",   "name"),
    ]
    for rel_type, target_label, target_key in rel_queries:
        try:
            result = conn.execute(f"""
                MATCH (m:Memory {{path: $p}})-[:{rel_type}]->(t:{target_label})
                RETURN t.{target_key} AS target
            """, {"p": memory_path})
            df = result.get_as_df()
            for val in df["target"].tolist():
                edges.append({"rel": rel_type, "target_type": target_label, "target": val})
        except Exception:
            continue

    return edges


def get_vector_chunks(memory_path: str) -> list[dict]:
    """查詢 ChromaDB 中此記憶的 chunk 數量與 prefix。"""
    try:
        from vectorize import get_collection
        _, col = get_collection()
        results = col.get(
            where={"path": {"$eq": memory_path}},
            include=["documents", "metadatas"],
        )
        chunks = []
        for doc, meta in zip(results["documents"], results["metadatas"]):
            # 取第一行（prefix）
            prefix = doc.split("\n")[0] if "\n" in doc else doc[:150]
            chunks.append({
                "id": f"{meta.get('chunk_type', '?')}#{meta.get('chunk_index', '?')}",
                "prefix": prefix,
            })
        return chunks
    except Exception:
        return []


def inspect_memory(keyword: str):
    """透視一筆記憶的完整追溯鏈。"""
    matches = find_memories(keyword)
    if not matches:
        print(f"\n  The Augury sees nothing for「{keyword}」. No echoes in the Vault.\n")
        return

    print(f"\n  The Augury found {len(matches)} memory fragment(s) for「{keyword}」:\n")

    for path in matches:
        rel_path = str(path.relative_to(BASE))
        print(f"  {'═' * 60}")
        print(f"  📜 {rel_path}")
        print(f"  {'─' * 60}")

        fm = parse_enrichment_from_file(path)
        if not fm:
            print("     (No frontmatter found)")
            continue

        # ── Enrichment 欄位 ──
        if fm.get("enriched_at"):
            print(f"  [Enrichment]")
            print(f"     enriched_at : {fm.get('enriched_at', '?')}")
            print(f"     importance  : {fm.get('importance', '?')}")
            print(f"     period      : {fm.get('period', '(empty)')}")
            print(f"     themes      : {fm.get('themes', [])}")

            ents = fm.get("entities", {})
            if isinstance(ents, dict):
                print(f"     locations   : {ents.get('locations', [])}")
                print(f"     people      : {ents.get('people', [])}")
                print(f"     events      : {ents.get('events', [])}")
                print(f"     emotions    : {ents.get('emotions', [])}")
            else:
                print(f"     entities    : {ents}")

            facts = fm.get("personal_facts", [])
            if facts:
                print(f"     personal_facts:")
                for f_ in facts:
                    print(f"       - {f_}")
        else:
            print("  [Enrichment] Not enriched yet.")

        # ── Tapestry 邊 ──
        print(f"\n  [Tapestry Edges]")
        edges = get_tapestry_edges(rel_path)
        if edges:
            for e in edges:
                print(f"     {e['rel']:15s} → {e['target_type']}:「{e['target']}」")
        else:
            print("     (No edges found)")

        # ── Vector Chunks ──
        print(f"\n  [Vector Index]")
        chunks = get_vector_chunks(rel_path)
        if chunks:
            print(f"     {len(chunks)} chunk(s):")
            for c in chunks[:3]:
                print(f"       [{c['id']}] {c['prefix'][:80]}")
            if len(chunks) > 3:
                print(f"       ... and {len(chunks) - 3} more")
        else:
            print("     (No chunks found)")

        print()


# ═══════════════════════════════════════════════════════════════
#  II. CORRECT — 手動修正 + 連鎖更新
# ═══════════════════════════════════════════════════════════════

def _read_file_content(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rewrite_enrichment_field(path: Path, field: str, action: str, value: str) -> bool:
    """
    修改 enrichment YAML 中的特定欄位。

    field:  "locations" | "people" | "events" | "emotions" | "period" | "themes" | "personal_facts"
    action: "add" | "remove" | "set"
    value:  要操作的值
    """
    import yaml

    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        print(f"  [ERROR] {path.name} has no frontmatter.")
        return False

    end = content.find("\n---", 3)
    if end < 0:
        return False

    raw_fm = content[3:end]
    body = content[end + 4:]

    # 用 yaml 解析
    clean = [ln for ln in raw_fm.split("\n") if not ln.strip().startswith("#")]
    try:
        fm = yaml.safe_load("\n".join(clean)) or {}
    except Exception as e:
        print(f"  [ERROR] YAML parse failed: {e}")
        return False

    if not isinstance(fm, dict):
        return False

    # 根據 field 定位並修改
    entities_fields = {"locations", "people", "events", "emotions"}

    if field in entities_fields:
        ents = fm.get("entities", {})
        if not isinstance(ents, dict):
            ents = {}
        current = ents.get(field, [])
        if not isinstance(current, list):
            current = []

        if action == "add" and value not in current:
            current.append(value)
        elif action == "remove" and value in current:
            current.remove(value)
        elif action == "set":
            current = [v.strip() for v in value.split(",") if v.strip()]

        ents[field] = current
        fm["entities"] = ents

    elif field == "period":
        if action == "set":
            fm["period"] = value
        elif action == "remove":
            fm["period"] = ""

    elif field == "themes":
        current = fm.get("themes", [])
        if not isinstance(current, list):
            current = []
        if action == "add" and value not in current:
            current.append(value)
        elif action == "remove" and value in current:
            current.remove(value)
        elif action == "set":
            current = [v.strip() for v in value.split(",") if v.strip()]
        fm["themes"] = current

    elif field == "personal_facts":
        current = fm.get("personal_facts", [])
        if not isinstance(current, list):
            current = []
        if action == "add" and value not in current:
            current.append(value)
        elif action == "remove":
            current = [f for f in current if value not in f]
        fm["personal_facts"] = current

    else:
        print(f"  [ERROR] Unknown field: {field}")
        return False

    # ── 重新寫回 YAML（使用 enrich.py 相同格式）──
    # 移除舊 enrichment block
    fm_block = content[3:end]
    fm_block = re.sub(
        r'\n# ── Enrichment.*?(?=\n[a-z]|\Z)', '', fm_block, flags=re.DOTALL
    )

    ents = fm.get("entities", {})
    locs     = json.dumps(ents.get("locations", []),  ensure_ascii=False)
    people   = json.dumps(ents.get("people", []),     ensure_ascii=False)
    events   = json.dumps(ents.get("events", []),     ensure_ascii=False)
    emotions = json.dumps(ents.get("emotions", []),   ensure_ascii=False)
    themes   = json.dumps(fm.get("themes", []),       ensure_ascii=False)
    period   = fm.get("period", "")
    imp      = fm.get("importance", "medium")
    facts    = json.dumps(fm.get("personal_facts", []), ensure_ascii=False)
    now      = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    enrich_block = f"""
# ── Enrichment（LLM 語意增強，僅含原文出現的實體）──
enriched_at: "{now}"
importance: {imp}
period: "{period}"
themes: {themes}
personal_facts: {facts}
entities:
  locations: {locs}
  people: {people}
  events: {events}
  emotions: {emotions}"""

    new_content = f"---{fm_block}{enrich_block}\n---\n\n{body.lstrip()}"
    path.write_text(new_content, encoding="utf-8")
    return True


def _cascade_update(path: Path, rel_path: str):
    """
    修正 YAML 後，連鎖更新 Tapestry 和向量索引。

    步驟：
    1. 從修正後的 YAML 重新讀取 enrichment
    2. 清除此記憶在 Tapestry 中的所有舊邊 → 重新織入
    3. 清除此記憶在 ChromaDB 中的所有舊 chunk → 重建
    4. 重建 BM25 索引（全量，因為 pickle 不支援增量刪除）
    """
    import yaml

    print(f"  ── Cascade Update for {rel_path} ──")

    # ── 1. 讀取修正後的 enrichment ──
    fm = parse_enrichment_from_file(path)
    if not fm:
        print("  [ERROR] Cannot read frontmatter after correction.")
        return

    ents = fm.get("entities", {})
    if not isinstance(ents, dict):
        ents = {}
    enrichment = {
        "entities": {
            "locations": ents.get("locations", []),
            "people":    ents.get("people", []),
            "events":    ents.get("events", []),
        },
        "period":         fm.get("period", "") or "",
        "personal_facts": fm.get("personal_facts", []) or [],
    }

    # ── 2. Tapestry：清除舊邊 → 重新織入 ──
    try:
        from tapestry import get_conn, weave_memory

        conn = get_conn()
        # 刪除此 Memory 的所有出邊
        for rel in ["mem_location", "mem_person", "mem_event", "mem_period"]:
            try:
                conn.execute(f"""
                    MATCH (m:Memory {{path: $p}})-[r:{rel}]->() DELETE r
                """, {"p": rel_path})
            except Exception:
                pass

        # 重新織入
        weave_memory(conn, rel_path, enrichment)
        print("     ✦ Tapestry: edges rewoven.")
    except ImportError:
        print("     [WARN] Tapestry not available, skipping.")
    except Exception as e:
        print(f"     [WARN] Tapestry update failed: {e}")

    # ── 3. ChromaDB：清除舊 chunks → 重建 ──
    try:
        from vectorize import get_collection, parse_frontmatter, build_chunks

        _, col = get_collection()
        # 刪除此檔案的所有舊 chunks
        old_results = col.get(where={"path": {"$eq": rel_path}}, include=[])
        if old_results["ids"]:
            col.delete(ids=old_results["ids"])

        # 重建 chunks
        content = path.read_text(encoding="utf-8")
        fm_parsed, body = parse_frontmatter(content)
        chunks = build_chunks(rel_path, fm_parsed, body)
        if chunks:
            col.add(
                ids       = [c["id"]   for c in chunks],
                documents = [c["text"] for c in chunks],
                metadatas = [c["meta"] for c in chunks],
            )
        print(f"     ✦ ChromaDB: {len(chunks)} chunk(s) re-indexed.")
    except Exception as e:
        print(f"     [WARN] ChromaDB update failed: {e}")

    # ── 4. BM25：全量重建 ──
    try:
        from vectorize import collect_all_chunks, build_bm25_index
        all_chunks = collect_all_chunks()
        build_bm25_index(all_chunks)
        print("     ✦ BM25: index rebuilt.")
    except Exception as e:
        print(f"     [WARN] BM25 rebuild failed: {e}")

    print()


def correct_memory(file_path: str, corrections: list[tuple[str, str, str]]):
    """
    執行記憶修正。

    corrections: list of (field, action, value)
      e.g. [("locations", "remove", "台灣"), ("locations", "add", "深圳")]
    """
    p = (BASE / file_path) if not Path(file_path).is_absolute() else Path(file_path)
    if not p.exists():
        print(f"\n  The Augury cannot find: {file_path}")
        print(f"  (Searched: {p})\n")
        return

    rel_path = str(p.relative_to(BASE))
    print(f"\n  ── The Augury corrects: {rel_path} ──\n")

    # 顯示修正前
    print("  [Before]")
    fm = parse_enrichment_from_file(p)
    if fm:
        ents = fm.get("entities", {})
        if isinstance(ents, dict):
            print(f"     locations: {ents.get('locations', [])}")
            print(f"     people:    {ents.get('people', [])}")
        print(f"     period:    {fm.get('period', '')}")

    # 執行修正
    for field, action, value in corrections:
        ok = _rewrite_enrichment_field(p, field, action, value)
        symbol = "✦" if ok else "✗"
        print(f"\n  {symbol} {action} {field}: {value}")

    # 顯示修正後
    print(f"\n  [After]")
    fm = parse_enrichment_from_file(p)
    if fm:
        ents = fm.get("entities", {})
        if isinstance(ents, dict):
            print(f"     locations: {ents.get('locations', [])}")
            print(f"     people:    {ents.get('people', [])}")
        print(f"     period:    {fm.get('period', '')}")

    # 連鎖更新
    print()
    _cascade_update(p, rel_path)

    print("  🔮 The Augury's correction is complete. The record is mended.\n")


# ═══════════════════════════════════════════════════════════════
#  III. PATROL — 全量巡迴審計
# ═══════════════════════════════════════════════════════════════

PATROL_PROMPT = """\
You are the Augur of Memosyne — a meticulous auditor of memory enrichments.

You are given a memory's ORIGINAL TEXT and its current ENRICHMENT metadata.
Your duty: verify every enrichment field against the original text.

AUDIT RULES:
1. Every entity in locations/people/events/emotions MUST appear literally in the original text
2. Locations must be SEMANTICALLY correct — if text says "在深圳工作" but enrichment says "台灣", that's WRONG
3. Period should reflect the actual life-stage described, not be fabricated
4. Personal facts must be true statements found in the text
5. Check for MISSING entities that should have been extracted
6. Check for HALLUCINATED entities that don't belong

For each issue found, report:
- field: which enrichment field has the problem
- issue_type: "hallucination" | "wrong_association" | "missing" | "inaccurate"
- current_value: what's currently in the enrichment
- suggested_value: what it should be (or "" to remove, or the missing entity to add)
- reason: brief explanation in Traditional Chinese

If everything is correct, return an empty issues array.

Respond in PURE JSON only:
{{
  "status": "ok" | "issues_found",
  "issues": [
    {{
      "field": "locations",
      "issue_type": "hallucination",
      "current_value": "台灣",
      "suggested_value": "",
      "reason": "原文描述的是深圳的工作經歷，未提及台灣"
    }}
  ]
}}

Memory file: {file_path}
Memory title: {title}

── ORIGINAL TEXT ──
{original_text}

── CURRENT ENRICHMENT ──
{enrichment_json}
"""


def _call_patrol_llm(file_path: str, title: str, original_text: str,
                     enrichment: dict, model: str) -> dict:
    """呼叫 LLM 審計一筆 enrichment。"""
    import ollama

    # 截斷過長內容
    text_trimmed = original_text[:3000]
    if len(original_text) > 3000:
        text_trimmed += "\n...[截斷]"

    enrichment_json = json.dumps(enrichment, ensure_ascii=False, indent=2)

    prompt = PATROL_PROMPT.format(
        file_path=file_path,
        title=title,
        original_text=text_trimmed,
        enrichment_json=enrichment_json,
    )

    resp = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        think=False,
        options={"temperature": 0},
    )
    raw = resp["message"]["content"].strip()

    start = raw.find("{")
    end   = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"LLM 沒有回傳 JSON：{raw[:300]}")
    json_str = raw[start:end + 1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        cleaned = re.sub(r',\s*([}\]])', r'\1', json_str)
        cleaned = cleaned.replace("'", '"')
        return json.loads(cleaned)


def collect_enriched_files() -> list[Path]:
    """收集所有已 enriched 的記憶檔案。"""
    EXCLUDE_DIRS = {"00_System"}
    files = []
    for md in sorted(BASE.rglob("*.md")):
        parts = md.relative_to(BASE).parts
        if parts[0] in EXCLUDE_DIRS:
            continue
        if md.name in {"README.md", ".cursorrules"}:
            continue
        content = md.read_text(encoding="utf-8")
        if "enriched_at:" in content:
            files.append(md)
    return files


def patrol_all(model: str, dry_run: bool = False):
    """
    The Augury Patrol — 巡迴所有記憶，審計 enrichment。
    """
    import yaml

    files = collect_enriched_files()
    total = len(files)
    issues_total = 0
    report_entries = []

    print(f"\n  ╔══════════════════════════════════════════════════════╗")
    print(f"  ║  🔮  The Augury Patrol — Divining Memory Health  🔮   ║")
    print(f"  ╚══════════════════════════════════════════════════════╝\n")
    print(f"  Scanning {total} enriched memories with model: {model}")
    if dry_run:
        print(f"  ⚠  Dry-run mode — report only, no corrections.\n")
    print()

    for i, path in enumerate(files, 1):
        rel_path = str(path.relative_to(BASE))
        content = path.read_text(encoding="utf-8")

        # 解析 frontmatter
        if not content.startswith("---"):
            continue
        end = content.find("\n---", 3)
        if end < 0:
            continue
        raw_fm = content[3:end]
        body = content[end + 4:].strip()

        clean = [ln for ln in raw_fm.split("\n") if not ln.strip().startswith("#")]
        try:
            fm = yaml.safe_load("\n".join(clean)) or {}
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue

        title = fm.get("title", path.stem)
        ents = fm.get("entities", {})
        if not isinstance(ents, dict):
            ents = {}

        enrichment = {
            "entities": {
                "locations": ents.get("locations", []),
                "people":    ents.get("people", []),
                "events":    ents.get("events", []),
                "emotions":  ents.get("emotions", []),
            },
            "themes":         fm.get("themes", []),
            "period":         fm.get("period", ""),
            "importance":     fm.get("importance", "medium"),
            "personal_facts": fm.get("personal_facts", []),
        }

        print(f"  [{i}/{total}] {rel_path} ... ", end="", flush=True)

        try:
            result = _call_patrol_llm(rel_path, title, f"{title}\n{body}",
                                      enrichment, model)
            status = result.get("status", "ok")
            issues = result.get("issues", [])

            if status == "ok" or not issues:
                print("✓ OK")
            else:
                print(f"⚠ {len(issues)} issue(s)")
                issues_total += len(issues)
                for issue in issues:
                    print(f"       [{issue.get('issue_type', '?')}] "
                          f"{issue.get('field', '?')}: "
                          f"「{issue.get('current_value', '')}」→ "
                          f"「{issue.get('suggested_value', '')}」")
                    print(f"        理由：{issue.get('reason', '')}")

                report_entries.append({
                    "file": rel_path,
                    "title": title,
                    "issues": issues,
                })
        except Exception as e:
            print(f"ERROR: {e}")

    # ── 輸出報告 ──
    print(f"\n  {'═' * 55}")
    print(f"  The Augury Patrol Complete.")
    print(f"  Scanned: {total} memories  |  Issues found: {issues_total}")

    if report_entries:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = REPORT_DIR / f"augury_report_{ts}.json"
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "model": model,
            "total_scanned": total,
            "total_issues": issues_total,
            "entries": report_entries,
        }
        report_path.write_text(
            json.dumps(report_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"  Report saved: {report_path.relative_to(BASE)}")
        print(f"\n  To apply corrections:")
        print(f"    python3 augury.py --apply-report {report_path.name}")
    else:
        print(f"\n  🌊 All memories are in harmony. The Augury is pleased.")

    print()


# ═══════════════════════════════════════════════════════════════
#  IV. APPLY REPORT — 從報告批次修正
# ═══════════════════════════════════════════════════════════════

def apply_report(report_file: str, auto_confirm: bool = False):
    """
    讀取 patrol 報告，逐一顯示問題並提示是否修正。
    """
    # 搜尋報告路徑
    rp = Path(report_file)
    if not rp.exists():
        rp = REPORT_DIR / report_file
    if not rp.exists():
        print(f"\n  Report not found: {report_file}\n")
        return

    report = json.loads(rp.read_text(encoding="utf-8"))
    entries = report.get("entries", [])
    if not entries:
        print(f"\n  Report has no issues to apply.\n")
        return

    print(f"\n  ── Applying Augury Report: {rp.name} ──")
    print(f"  {len(entries)} file(s) with issues.\n")

    applied = 0
    skipped = 0

    for entry in entries:
        file_path = entry["file"]
        title = entry.get("title", "")
        issues = entry.get("issues", [])

        p = BASE / file_path
        if not p.exists():
            print(f"  [SKIP] File not found: {file_path}")
            skipped += 1
            continue

        print(f"\n  📜 {file_path} ({title})")
        print(f"  {'─' * 50}")

        corrections = []
        for issue in issues:
            field      = issue.get("field", "")
            issue_type = issue.get("issue_type", "")
            current    = issue.get("current_value", "")
            suggested  = issue.get("suggested_value", "")
            reason     = issue.get("reason", "")

            print(f"  [{issue_type}] {field}: 「{current}」→「{suggested}」")
            print(f"    理由：{reason}")

            # 決定修正動作
            if issue_type in ("hallucination", "wrong_association") and current:
                corrections.append((field, "remove", current))
                if suggested:
                    corrections.append((field, "add", suggested))
            elif issue_type == "missing" and suggested:
                corrections.append((field, "add", suggested))
            elif issue_type == "inaccurate":
                if current:
                    corrections.append((field, "remove", current))
                if suggested:
                    corrections.append((field, "add", suggested))

        if not corrections:
            print("  (No actionable corrections)")
            continue

        # 確認
        if not auto_confirm:
            print(f"\n  Corrections to apply: {len(corrections)}")
            for c in corrections:
                print(f"    {c[1]:6s} {c[0]}: {c[2]}")
            answer = input("  Apply? [y/N/a(ll)] ").strip().lower()
            if answer == "a":
                auto_confirm = True
            elif answer != "y":
                print("  Skipped.")
                skipped += 1
                continue

        # 執行修正
        for field, action, value in corrections:
            _rewrite_enrichment_field(p, field, action, value)

        rel_path = str(p.relative_to(BASE))
        _cascade_update(p, rel_path)
        applied += 1

    print(f"\n  ── Report Application Complete ──")
    print(f"  Applied: {applied}  |  Skipped: {skipped}")
    print()


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="Memosyne — The Augury (Memory Audit & Correction)"
    )

    # 模式選擇
    ap.add_argument("--inspect", type=str, default="",
                    help="透視記憶追溯鏈（關鍵字搜尋）")
    ap.add_argument("--correct", type=str, default="",
                    help="修正記憶（檔案路徑，相對 BASE）")
    ap.add_argument("--patrol", action="store_true",
                    help="全量巡迴審計所有記憶")
    ap.add_argument("--apply-report", type=str, default="",
                    help="從 patrol 報告批次修正")

    # 修正選項
    ap.add_argument("--remove-location", action="append", default=[],
                    help="移除 location（可多次使用）")
    ap.add_argument("--add-location", action="append", default=[],
                    help="新增 location（可多次使用）")
    ap.add_argument("--remove-person", action="append", default=[],
                    help="移除 person")
    ap.add_argument("--add-person", action="append", default=[],
                    help="新增 person")
    ap.add_argument("--set-period", type=str, default="",
                    help="設定 period")
    ap.add_argument("--remove-event", action="append", default=[],
                    help="移除 event")
    ap.add_argument("--add-event", action="append", default=[],
                    help="新增 event")
    ap.add_argument("--remove-fact", action="append", default=[],
                    help="移除 personal_fact（部分匹配）")
    ap.add_argument("--add-fact", action="append", default=[],
                    help="新增 personal_fact")

    # 通用選項
    ap.add_argument("--model", default="gemma4:26b",
                    help="Patrol 使用的 LLM 模型（預設 gemma4:26b）")
    ap.add_argument("--dry-run", action="store_true",
                    help="預覽模式，不實際修正")
    ap.add_argument("--yes", action="store_true",
                    help="apply-report 時自動確認所有修正")

    args = ap.parse_args()

    # ── Inspect ──
    if args.inspect:
        inspect_memory(args.inspect)
        return

    # ── Correct ──
    if args.correct:
        corrections = []
        for v in args.remove_location:
            corrections.append(("locations", "remove", v))
        for v in args.add_location:
            corrections.append(("locations", "add", v))
        for v in args.remove_person:
            corrections.append(("people", "remove", v))
        for v in args.add_person:
            corrections.append(("people", "add", v))
        for v in args.remove_event:
            corrections.append(("events", "remove", v))
        for v in args.add_event:
            corrections.append(("events", "add", v))
        for v in args.remove_fact:
            corrections.append(("personal_facts", "remove", v))
        for v in args.add_fact:
            corrections.append(("personal_facts", "add", v))
        if args.set_period:
            corrections.append(("period", "set", args.set_period))

        if not corrections:
            print("\n  No corrections specified. Use --add-location, --remove-location, etc.")
            print("  Example:")
            print('    python3 augury.py --correct 30_Journal/2024/workus.md \\')
            print('      --remove-location "台灣" --add-location "深圳"\n')
            return

        correct_memory(args.correct, corrections)
        return

    # ── Patrol ──
    if args.patrol:
        patrol_all(model=args.model, dry_run=args.dry_run)
        return

    # ── Apply Report ──
    if args.apply_report:
        apply_report(args.apply_report, auto_confirm=args.yes)
        return

    # ── 無指令 ──
    ap.print_help()


if __name__ == "__main__":
    main()
