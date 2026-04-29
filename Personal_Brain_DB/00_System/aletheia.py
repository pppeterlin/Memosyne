#!/usr/bin/env python3
"""
Aletheia — 對話式記憶更正（Phase 6）

Aletheia（希臘語 ἀλήθεια，「真實 / 揭露」）。當記憶在 Oracle 入庫後被發現
有誤、過時、或遺漏事實，使用者透過對話介面直接指示 Aletheia 做修正。

設計原則：
1. Reversible — 所有操作寫入 aletheia_log.jsonl，保留 before/after
2. Dry-run 為預設 — apply 需要顯式 --apply
3. 最小侵入 — 只動 frontmatter 的 personal_facts / themes，或對 body
   做受限 substring 替換；結構性改動（合併實體）交由 Naming Rite
4. 後續索引重建 — 修改後需重跑 vectorize --rebuild 才會反映於搜尋

支援操作：
  ADD_FACT        在 personal_facts 新增一條事實
  UPDATE_FACT     取代指定事實（by index 或 by substring match）
  INVALIDATE_FACT 移除事實（log 保留以便 revert）
  CORRECT_TEXT    在 body 做 literal substring 替換（old 必須唯一出現）
  REVERT          讀 log entry 反向操作

用法：
  python3 aletheia.py --show <path>                          # 顯示當前狀態
  python3 aletheia.py --add <path> --fact "我的眼鏡是 KMN-9503"
  python3 aletheia.py --update <path> --old "... 2024" --new "... 2025"
  python3 aletheia.py --invalidate <path> --match "住在 Tokyo"
  python3 aletheia.py --correct <path> --old "X" --new "Y"   # body 替換
  python3 aletheia.py --revert <log_id>
  # 加 --apply 才實際寫入，預設 dry-run
"""
from __future__ import annotations
import argparse
import difflib
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parent
BASE = SYSTEM_DIR.parent
sys.path.insert(0, str(SYSTEM_DIR))

ALETHEIA_LOG = SYSTEM_DIR / "aletheia_log.jsonl"
ALETHEIA_BACKUP_DIR = SYSTEM_DIR / "aletheia_backup"
PENDING_REEMBED = SYSTEM_DIR / "aletheia_pending_reembed.json"


# ── Safety net (Phase 6.4) ────────────────────────────────
def _snapshot(full: Path, log_id: str) -> Path | None:
    """Apply 前把檔案 shadow-copy 到 aletheia_backup/；失敗不阻斷。"""
    try:
        import shutil
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_dir = ALETHEIA_BACKUP_DIR / f"{stamp}_{log_id}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / full.name
        shutil.copy2(full, dest)
        return dest
    except Exception as e:
        print(f"⚠️  snapshot failed: {e}", file=sys.stderr)
        return None


def _mark_pending_reembed(rel_path: str, reason: str) -> None:
    """body 動過的 memory 記錄到 pending list；之後跑
    vectorize.py --rebuild 才會更新索引。"""
    try:
        data = {}
        if PENDING_REEMBED.exists():
            data = json.loads(PENDING_REEMBED.read_text(encoding="utf-8"))
        entry = data.setdefault(rel_path, {"count": 0, "reasons": []})
        entry["count"] += 1
        entry["reasons"].append({"reason": reason, "at": datetime.now().isoformat()})
        entry["last_marked"] = datetime.now().isoformat()
        PENDING_REEMBED.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except Exception as e:
        print(f"⚠️  mark pending reembed failed: {e}", file=sys.stderr)


def _is_high_risk_correct(old: str, new: str) -> bool:
    """CORRECT_TEXT 的啟發式高風險判斷：
    - 替換字串 > 120 字
    - 跨 >= 3 行
    - 新舊長度差 > 200 字
    """
    if len(old) > 120 or len(new) > 120:
        return True
    if old.count("\n") >= 2 or new.count("\n") >= 2:
        return True
    if abs(len(old) - len(new)) > 200:
        return True
    return False


# ── Tapestry sync (Phase 6.3) ─────────────────────────────
def _extract_enrichment(fm_block: str) -> dict:
    """從 frontmatter 抽 enrichment 欄位（entities / personal_facts / period）。
    容忍缺失 PyYAML，退回空 enrichment。"""
    try:
        import yaml
        data = yaml.safe_load(fm_block) or {}
        return {
            "entities": data.get("entities", {}) or {},
            "personal_facts": data.get("personal_facts", []) or [],
            "period": data.get("period", "") or "",
        }
    except Exception:
        return {"entities": {}, "personal_facts": [], "period": ""}


def _memory_path_for_tapestry(path: str, full: Path) -> str:
    """Tapestry 用的 path key：相對於 vault 根，剝掉 _vault/ 前綴。"""
    try:
        rel = str(full.relative_to(BASE))
    except ValueError:
        rel = path
    if rel.startswith("_vault/"):
        rel = rel[len("_vault/"):]
    return rel


def _sync_tapestry(entry: dict, full: Path, new_content: str) -> dict:
    """將 Aletheia 操作同步至 Tapestry。
    - ADD_FACT / UPDATE_FACT / CORRECT_TEXT → 重新 weave（MERGE 冪等）
    - INVALIDATE_FACT → invalidate evidence 匹配的 person_loc 邊
    失敗不阻斷主操作，回傳 sync_status。"""
    op = entry["op"]
    path = entry["path"]
    status: dict = {"synced": False, "detail": ""}
    try:
        import tapestry as T
        from datetime import datetime as _dt
        conn = T.get_conn()
        mp = _memory_path_for_tapestry(path, full)
        ts = _dt.now()

        if op in ("ADD_FACT", "UPDATE_FACT", "CORRECT_TEXT"):
            fm_block, _ = _split_fm(new_content)
            if fm_block is None:
                status["detail"] = "no_frontmatter"
                return status
            enr = _extract_enrichment(fm_block)
            T.weave_memory(conn, mp, enr, now=ts)
            status.update(synced=True, detail=f"rewove memory={mp}")

        elif op == "INVALIDATE_FACT":
            # 找 evidence 與 removed fact 的前 80 字元重疊的 person_loc 邊
            removed = entry.get("removed", "")
            ev_prefix = removed[:80]
            rows = conn.execute(
                "MATCH (p:Person)-[e:person_loc]->(l:Location) "
                "WHERE e.evidence = $ev AND e.t_valid_end IS NULL "
                "RETURN p.name AS pn, l.name AS ln",
                {"ev": ev_prefix},
            )
            invalidated = 0
            while rows.has_next():
                r = rows.get_next()
                pn, ln = r[0], r[1]
                T.invalidate_edge(
                    conn, "person_loc", "Person", pn, "Location", ln,
                    invalidated_by=f"aletheia:{entry.get('id', '?')}", when=ts,
                )
                invalidated += 1
            status.update(synced=True, detail=f"invalidated {invalidated} person_loc edges")

        elif op == "REVERT":
            status["detail"] = "revert_delegates_to_inverse_op"
    except Exception as e:
        status["detail"] = f"sync_failed: {type(e).__name__}: {e}"
    return status


# ── Frontmatter utilities ─────────────────────────────────
def _split_fm(content: str) -> tuple[str | None, str]:
    """回傳 (fm_block_without_delim, body_from_'---\\n' after fm)。
    無 frontmatter 回傳 (None, content)。"""
    if not content.startswith("---"):
        return None, content
    end = content.find("\n---", 3)
    if end < 0:
        return None, content
    fm_block = content[4:end]          # skip leading '---\n'
    after = content[end + 4:]          # skip '\n---'
    if after.startswith("\n"):
        after = after[1:]
    return fm_block, after


def _assemble(fm_block: str, body: str) -> str:
    return f"---\n{fm_block}\n---\n{body}"


def _get_personal_facts(fm_block: str) -> list[str]:
    """解析 YAML-ish personal_facts list。只處理單行 flow 或多行 dash 格式。"""
    m = re.search(r'^personal_facts:\s*(.*)$', fm_block, re.MULTILINE)
    if not m:
        return []
    rest = m.group(1).strip()
    if rest.startswith("["):
        # flow list: ["a", "b"]
        try:
            return json.loads(rest)
        except Exception:
            return []
    # block list on subsequent lines:
    #   personal_facts:
    #     - "a"
    #     - "b"
    facts = []
    lines = fm_block.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("personal_facts:"):
            start = i + 1
            break
    if start is None:
        return []
    for ln in lines[start:]:
        s = ln.rstrip()
        if re.match(r'^\s*-\s+', s):
            val = s.split("-", 1)[1].strip().strip('"').strip("'")
            facts.append(val)
        elif s and not s.startswith(" "):
            break
    return facts


def _set_personal_facts(fm_block: str, facts: list[str]) -> str:
    """覆寫 personal_facts 為 JSON flow list（簡化，與 enrich.py 相容）。"""
    new_line = f"personal_facts: {json.dumps(facts, ensure_ascii=False)}"
    if re.search(r'^personal_facts:', fm_block, re.MULTILINE):
        # 取代整個 personal_facts 區塊（單行或多行 block list）
        lines = fm_block.splitlines()
        out: list[str] = []
        i = 0
        while i < len(lines):
            if lines[i].startswith("personal_facts:"):
                out.append(new_line)
                i += 1
                # skip block list items that follow
                while i < len(lines) and (
                    re.match(r'^\s+-\s+', lines[i]) or lines[i].strip() == ""
                ):
                    if lines[i].strip() == "":
                        break
                    i += 1
                continue
            out.append(lines[i])
            i += 1
        return "\n".join(out)
    return fm_block.rstrip() + "\n" + new_line


# ── Log (reversible) ──────────────────────────────────────
def _log(entry: dict) -> str:
    entry = dict(entry)
    entry["id"] = entry.get("id") or uuid.uuid4().hex[:12]
    entry["timestamp"] = datetime.now().isoformat()
    with ALETHEIA_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry["id"]


def _find_log(log_id: str) -> dict | None:
    if not ALETHEIA_LOG.exists():
        return None
    for line in ALETHEIA_LOG.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("id") == log_id:
            return e
    return None


# ── Operations ────────────────────────────────────────────
def _resolve_path(path: str) -> Path:
    """允許使用者輸入相對於 vault 根（如 20_AI_Chats/... 或 _vault/...）。"""
    p = BASE / path
    if p.exists():
        return p
    p2 = BASE / "_vault" / path
    if p2.exists():
        return p2
    raise FileNotFoundError(f"記憶不存在：{path}")


def _show_diff(before: str, after: str, path: str) -> None:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{path} (before)",
        tofile=f"{path} (after)",
        n=2,
    )
    sys.stdout.writelines(diff)


def add_fact(path: str, fact: str, apply: bool = False, sync: bool = True) -> dict:
    full = _resolve_path(path)
    content = full.read_text(encoding="utf-8")
    fm, body = _split_fm(content)
    if fm is None:
        raise ValueError("記憶缺少 frontmatter")
    facts = _get_personal_facts(fm)
    if fact in facts:
        return {"op": "ADD_FACT", "status": "noop_exists", "path": path}
    new_facts = facts + [fact]
    new_fm = _set_personal_facts(fm, new_facts)
    new_content = _assemble(new_fm, body)
    _show_diff(content, new_content, path)
    entry = {"op": "ADD_FACT", "path": path, "before": facts, "after": new_facts,
             "added": fact}
    if apply:
        entry["id"] = uuid.uuid4().hex[:12]
        snap = _snapshot(full, entry["id"])
        if snap: entry["snapshot"] = str(snap.relative_to(SYSTEM_DIR))
        full.write_text(new_content, encoding="utf-8")
        _log(entry)
        if sync:
            entry["sync"] = _sync_tapestry(entry, full, new_content)
            print(f"🕸  tapestry: {entry['sync']['detail']}")
        print(f"\n✅ Applied (log_id={entry['id']})")
    else:
        print("\n⚠️  dry-run; 加 --apply 才實際寫入")
    return entry


def update_fact(path: str, old: str, new: str, apply: bool = False, sync: bool = True) -> dict:
    full = _resolve_path(path)
    content = full.read_text(encoding="utf-8")
    fm, body = _split_fm(content)
    if fm is None:
        raise ValueError("記憶缺少 frontmatter")
    facts = _get_personal_facts(fm)
    matches = [i for i, f in enumerate(facts) if old in f]
    if not matches:
        raise ValueError(f"personal_facts 中找不到 match：{old!r}")
    if len(matches) > 1:
        raise ValueError(f"多個事實 match {old!r}，請提供更精確的 substring")
    idx = matches[0]
    new_facts = list(facts)
    old_fact = new_facts[idx]
    new_facts[idx] = new
    new_fm = _set_personal_facts(fm, new_facts)
    new_content = _assemble(new_fm, body)
    _show_diff(content, new_content, path)
    entry = {"op": "UPDATE_FACT", "path": path, "index": idx,
             "before": old_fact, "after": new}
    if apply:
        entry["id"] = uuid.uuid4().hex[:12]
        snap = _snapshot(full, entry["id"])
        if snap: entry["snapshot"] = str(snap.relative_to(SYSTEM_DIR))
        full.write_text(new_content, encoding="utf-8")
        _log(entry)
        if sync:
            # 先 invalidate 舊 evidence 的 person_loc 邊，再 re-weave 新 fact
            inv_entry = {"op": "INVALIDATE_FACT", "path": path, "removed": old_fact,
                         "id": entry["id"]}
            _sync_tapestry(inv_entry, full, new_content)
            entry["sync"] = _sync_tapestry(
                {"op": "ADD_FACT", "path": path, "id": entry["id"]}, full, new_content,
            )
            print(f"🕸  tapestry: {entry['sync']['detail']}")
        print(f"\n✅ Applied (log_id={entry['id']})")
    else:
        print("\n⚠️  dry-run")
    return entry


def invalidate_fact(path: str, match: str, apply: bool = False, sync: bool = True) -> dict:
    full = _resolve_path(path)
    content = full.read_text(encoding="utf-8")
    fm, body = _split_fm(content)
    if fm is None:
        raise ValueError("記憶缺少 frontmatter")
    facts = _get_personal_facts(fm)
    matches = [i for i, f in enumerate(facts) if match in f]
    if not matches:
        raise ValueError(f"找不到 match：{match!r}")
    if len(matches) > 1:
        raise ValueError(f"多個 match，請提供更精確的 substring")
    idx = matches[0]
    removed = facts[idx]
    new_facts = [f for i, f in enumerate(facts) if i != idx]
    new_fm = _set_personal_facts(fm, new_facts)
    new_content = _assemble(new_fm, body)
    _show_diff(content, new_content, path)
    entry = {"op": "INVALIDATE_FACT", "path": path, "index": idx,
             "removed": removed, "before": facts, "after": new_facts}
    if apply:
        entry["id"] = uuid.uuid4().hex[:12]
        snap = _snapshot(full, entry["id"])
        if snap: entry["snapshot"] = str(snap.relative_to(SYSTEM_DIR))
        full.write_text(new_content, encoding="utf-8")
        _log(entry)
        if sync:
            entry["sync"] = _sync_tapestry(entry, full, new_content)
            print(f"🕸  tapestry: {entry['sync']['detail']}")
        print(f"\n✅ Applied (log_id={entry['id']})")
    else:
        print("\n⚠️  dry-run")
    return entry


def correct_text(path: str, old: str, new: str, apply: bool = False,
                 sync: bool = True, confirm: bool = False) -> dict:
    """Body 內 literal substring 替換。old 必須唯一出現以避免誤傷。
    高風險替換（長字串 / 跨行 / 大幅長度差）需要 confirm=True。"""
    full = _resolve_path(path)
    content = full.read_text(encoding="utf-8")
    fm, body = _split_fm(content)
    if body.count(old) == 0:
        raise ValueError(f"body 不含：{old!r}")
    if body.count(old) > 1:
        raise ValueError(f"body 中 {old!r} 出現 {body.count(old)} 次，"
                         "請提供更長、唯一的 substring")
    high_risk = _is_high_risk_correct(old, new)
    if apply and high_risk and not confirm:
        raise ValueError(
            "高風險 CORRECT_TEXT（長字串 / 跨行 / 大幅長度差）需加 --confirm 旗標",
        )
    new_body = body.replace(old, new, 1)
    new_content = _assemble(fm, new_body) if fm is not None else new_body
    _show_diff(content, new_content, path)
    entry = {"op": "CORRECT_TEXT", "path": path, "old": old, "new": new,
             "high_risk": high_risk}
    if apply:
        entry["id"] = uuid.uuid4().hex[:12]
        snap = _snapshot(full, entry["id"])
        if snap: entry["snapshot"] = str(snap.relative_to(SYSTEM_DIR))
        full.write_text(new_content, encoding="utf-8")
        _log(entry)
        # body 動了，務必 flag 重嵌
        rel = _memory_path_for_tapestry(path, full)
        _mark_pending_reembed(rel, reason=f"aletheia:CORRECT_TEXT:{entry['id']}")
        print(f"📎 marked {rel} as pending re-embed "
              "（跑 vectorize.py --rebuild 更新索引）")
        if sync:
            # body 動了，entity set 不變的機率高，但仍 re-weave 以防萬一
            entry["sync"] = _sync_tapestry(entry, full, new_content)
            print(f"🕸  tapestry: {entry['sync']['detail']}")
        print(f"\n✅ Applied (log_id={entry['id']})")
    else:
        print("\n⚠️  dry-run")
    return entry


def revert(log_id: str, apply: bool = False, sync: bool = True) -> dict:
    entry = _find_log(log_id)
    if not entry:
        raise ValueError(f"找不到 log id：{log_id}")
    op = entry["op"]
    path = entry["path"]
    if op == "ADD_FACT":
        return invalidate_fact(path, entry["added"], apply=apply, sync=sync)
    if op == "INVALIDATE_FACT":
        return add_fact(path, entry["removed"], apply=apply, sync=sync)
    if op == "UPDATE_FACT":
        return update_fact(path, entry["after"], entry["before"], apply=apply, sync=sync)
    if op == "CORRECT_TEXT":
        # revert of CORRECT_TEXT 本身可能也是高風險；預設 confirm=True（原操作已過 gate）
        return correct_text(path, entry["new"], entry["old"], apply=apply,
                            sync=sync, confirm=True)
    raise ValueError(f"不支援的 revert 操作：{op}")


def show(path: str) -> None:
    full = _resolve_path(path)
    content = full.read_text(encoding="utf-8")
    fm, _ = _split_fm(content)
    if fm is None:
        print("(無 frontmatter)")
        return
    facts = _get_personal_facts(fm)
    print(f"📜 {path}")
    print(f"personal_facts ({len(facts)}):")
    for i, f in enumerate(facts):
        print(f"  [{i}] {f}")


# ── CLI ──────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", metavar="PATH")
    ap.add_argument("--add", metavar="PATH")
    ap.add_argument("--update", metavar="PATH")
    ap.add_argument("--invalidate", metavar="PATH")
    ap.add_argument("--correct", metavar="PATH")
    ap.add_argument("--revert", metavar="LOG_ID")
    ap.add_argument("--fact", type=str, default="", help="ADD_FACT 用")
    ap.add_argument("--old", type=str, default="")
    ap.add_argument("--new", type=str, default="")
    ap.add_argument("--match", type=str, default="", help="INVALIDATE 用")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-sync", action="store_true",
                    help="跳過 Tapestry 同步（預設會同步）")
    ap.add_argument("--confirm", action="store_true",
                    help="高風險 CORRECT_TEXT 需要此旗標")
    args = ap.parse_args()
    sync = not args.no_sync

    try:
        if args.show:
            show(args.show)
        elif args.add:
            if not args.fact:
                ap.error("--add 需搭配 --fact")
            add_fact(args.add, args.fact, apply=args.apply, sync=sync)
        elif args.update:
            if not (args.old and args.new):
                ap.error("--update 需搭配 --old 和 --new")
            update_fact(args.update, args.old, args.new, apply=args.apply, sync=sync)
        elif args.invalidate:
            if not args.match:
                ap.error("--invalidate 需搭配 --match")
            invalidate_fact(args.invalidate, args.match, apply=args.apply, sync=sync)
        elif args.correct:
            if not (args.old and args.new):
                ap.error("--correct 需搭配 --old 和 --new")
            correct_text(args.correct, args.old, args.new, apply=args.apply,
                         sync=sync, confirm=args.confirm)
        elif args.revert:
            revert(args.revert, apply=args.apply, sync=sync)
        else:
            ap.print_help()
    except (ValueError, FileNotFoundError) as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
