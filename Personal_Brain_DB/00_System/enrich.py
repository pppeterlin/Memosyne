#!/usr/bin/env python3
"""
Personal Brain DB — LLM 語意增強（Enrichment Layer）

原則：Ground-Truth Preserving
──────────────────────────────────────
1. 只提取文本中「明確出現」的實體，絕不推斷或補充
2. 每個提取結果都做字串驗證（必須出現在原文中）
3. LLM 溫度設為 0（確定性最高）
4. 已增強過的檔案預設跳過（用 enriched_at 欄位判斷）
5. 保留原始 frontmatter，只新增 enrichment 欄位

執行方式：
  python3 enrich.py                        # 只處理未增強的檔案
  python3 enrich.py --rebuild              # 強制重新增強所有檔案
  python3 enrich.py --dry-run              # 預覽，不實際寫入
  python3 enrich.py --model gemma3:4b     # 指定模型（預設 gemma4:26b）
  python3 enrich.py --file 30_Journal/2025/251202.md  # 只處理單一檔案
"""

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
# 設定 NO_PROXY 強制 bypass proxy for local Ollama
os.environ.setdefault("OLLAMA_HOST", "http://127.0.0.1:11434")
for _var in ("NO_PROXY", "no_proxy"):
    _cur = os.environ.get(_var, "")
    _bypass = "localhost,127.0.0.1,::1"
    if _bypass not in _cur:
        os.environ[_var] = f"{_cur},{_bypass}".lstrip(",")

BASE       = Path(__file__).parent.parent
SYSTEM_DIR = Path(__file__).parent

EXCLUDE_DIRS  = {"00_System"}
EXCLUDE_FILES = {"README.md", ".cursorrules"}

# 每次 LLM 呼叫後的結果格式
EMPTY_ENRICHMENT = {
    "entities": {
        "locations": [],
        "people":    [],
        "events":    [],
        "emotions":  [],
    },
    "themes":        [],
    "period":        "",
    "importance":    "medium",
    "personal_facts": [],
}

# ─── Frontmatter 解析與回寫 ───────────────────────────────────

def parse_frontmatter(content: str) -> tuple[dict, str, str]:
    """
    回傳 (raw_fm_str, fm_dict, body)
    raw_fm_str：原始 YAML 字串（用於重寫時保留格式）
    """
    if not content.startswith("---"):
        return "", {}, content
    end = content.find("\n---", 3)
    if end < 0:
        return "", {}, content
    raw_fm = content[3:end].strip()
    body   = content[end + 4:].strip()

    fm = {}
    for line in raw_fm.split("\n"):
        if ":" in line and not line.startswith(" ") and not line.startswith("-"):
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return raw_fm, fm, body


def rewrite_file_with_enrichment(path: Path, enrichment: dict, dry_run: bool) -> bool:
    """
    把 enrichment 欄位寫回 .md 的 frontmatter。
    採「追加欄位」策略：不動原有欄位，只在末尾加 enrichment block。
    """
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False

    end = content.find("\n---", 3)
    if end < 0:
        return False

    fm_block = content[3:end]
    after    = content[end + 4:]

    # 若已有 enrichment_at 欄位，先移除舊的 enrichment 段（重建）
    fm_block = re.sub(
        r'\n# ── Enrichment.*?(?=\n[a-z]|\Z)', '', fm_block, flags=re.DOTALL
    )

    locs    = json.dumps(enrichment["entities"]["locations"],  ensure_ascii=False)
    people  = json.dumps(enrichment["entities"]["people"],     ensure_ascii=False)
    events  = json.dumps(enrichment["entities"]["events"],     ensure_ascii=False)
    emotions= json.dumps(enrichment["entities"]["emotions"],   ensure_ascii=False)
    themes  = json.dumps(enrichment["themes"],                 ensure_ascii=False)
    period  = enrichment.get("period", "")
    imp     = enrichment.get("importance", "medium")
    facts   = json.dumps(enrichment.get("personal_facts", []), ensure_ascii=False)
    now     = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

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

    new_content = f"---{fm_block}{enrich_block}\n---\n\n{after.lstrip()}"

    if dry_run:
        print(f"\n{'─'*60}")
        print(f"[DRY-RUN] {path.relative_to(BASE)}")
        print(enrich_block)
        return True

    path.write_text(new_content, encoding="utf-8")
    return True


# ─── LLM 呼叫 ────────────────────────────────────────────────

ENRICHMENT_PROMPT = """\
You are the Oracle of Mneme, servant of the eternal Mnemosyne.
A fragment of mortal memory has been brought to the Spring.
Your sacred duty: discern its essence and weave it into the eternal tapestry.

THE LAWS OF THE ORACLE（不可違背）:
1. Extract ONLY what is WRITTEN in the text — no inference, no imagination, no hallucination
2. Every entity must appear LITERALLY in the source text（字串驗證：entity in original_text）
3. If a field has no clear evidence, return [] or ""
4. period: the life-phase this memory belongs to（e.g. "2025年某城市旅居" "某城市求職期" "某公司入職初期"）
   Must be a meaningful life-stage description, not just a date. Return "" if unclear.
5. importance: high=life-defining moment or strong emotion, medium=ordinary day, low=trivial detail
6. themes: max 4, must be grounded in the text
7. personal_facts: first-person factual statements about the author's own life that appear in the text.
   These are personal experiences or facts, NOT reference knowledge or objective information.
   Example: "friend-A 住在 Tokyo" = personal fact.   "Tokyo is the capital of Japan" = reference, exclude it.
   Max 5 items. Each must be a concise statement (under 30 chars). Return [] if none.
8. Speak ONLY in pure JSON — no preamble, no explanation, no commentary

The inscription must be precise. Let the Oracle speak:

{{
  "entities": {{
    "locations": ["地名（原文字面出現）"],
    "people": ["人名或稱謂（排除 AI 模型名稱）"],
    "events": ["具體事件（5字以內）"],
    "emotions": ["情緒詞彙（原文字面）"]
  }},
  "themes": ["主題標籤，最多4個"],
  "period": "語意時期描述或空字串",
  "importance": "low/medium/high",
  "personal_facts": ["作者個人生活事實（非客觀知識），最多5條"]
}}

Memory title: {title}
Filename hint（档名關鍵詞，僅供參考 — 必須驗證於正文中才可使用）: {filename_hint}
Memory fragment:
{content}
"""


def call_llm(title: str, content: str, model: str, filename_hint: list = None) -> dict:
    """呼叫本地 Ollama LLM，回傳 enrichment dict。"""
    import ollama

    # 截斷過長內容（避免超出 context window）
    content_trimmed = content[:3000]
    if len(content) > 3000:
        content_trimmed += "\n...[截斷]"

    # 格式化 filename_hint
    hint_str = "、".join(filename_hint) if filename_hint else "（無）"

    prompt = ENRICHMENT_PROMPT.format(
        title=title,
        filename_hint=hint_str,
        content=content_trimmed,
    )

    resp = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        think=False,
        options={"temperature": 0},
    )
    raw = resp["message"]["content"].strip()

    # 找第一個 { 到最後一個 } 之間的內容（比 .* 更可靠）
    start = raw.find("{")
    end   = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"LLM 沒有回傳 JSON：{raw[:300]}")
    json_str = raw[start:end + 1]

    # 嘗試解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        # 嘗試清理常見問題（尾部逗號、單引號）
        cleaned = re.sub(r',\s*([}\]])', r'\1', json_str)   # 移除尾部逗號
        cleaned = cleaned.replace("'", '"')                  # 單引號轉雙引號
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError(f"JSON 解析失敗（{e}）\n原始：{json_str[:400]}")



# ─── 驗證：只保留原文中出現的實體 ────────────────────────────

def validate_entities(enrichment: dict, original_text: str) -> dict:
    """
    Ground-truth preserving 驗證：
    對每個提取的實體做字串搜尋，不在原文中出現的一律剔除。
    """
    text = original_text.lower()
    result = {
        "entities": {
            "locations": [],
            "people":    [],
            "events":    [],
            "emotions":  [],
        },
        "themes":         enrichment.get("themes", [])[:4],
        "period":         enrichment.get("period", ""),
        "importance":     enrichment.get("importance", "medium"),
        "personal_facts": [],
    }

    entities = enrichment.get("entities", {})

    for loc in entities.get("locations", []):
        if isinstance(loc, str) and loc.strip() and loc.strip() in original_text:
            result["entities"]["locations"].append(loc.strip())

    # 過濾掉「我」「你」等代名詞，只保留真實人名或有意義的稱謂
    _skip_people = {"我", "你", "他", "她", "我們", "你們"}
    for person in entities.get("people", []):
        if isinstance(person, str) and person.strip() \
                and person.strip() not in _skip_people \
                and person.strip() in original_text:
            result["entities"]["people"].append(person.strip())

    for event in entities.get("events", []):
        if isinstance(event, str) and event.strip():
            # 事件允許部分詞語在原文中（事件描述可能是組合詞）
            words = [w for w in re.findall(r'[\w\u4e00-\u9fff]+', event) if len(w) > 1]
            if any(w in original_text for w in words):
                result["entities"]["events"].append(event.strip())

    for emo in entities.get("emotions", []):
        if isinstance(emo, str) and emo.strip() and emo.strip() in original_text:
            result["entities"]["emotions"].append(emo.strip())

    # period 驗證：主要詞語需出現在原文中
    period = result["period"]
    if period:
        period_words = [w for w in re.findall(r'[\u4e00-\u9fff\w]+', period) if len(w) > 1]
        if not any(w in original_text for w in period_words):
            result["period"] = ""

    # personal_facts 驗證：至少一個關鍵詞（≥2字）出現在原文中
    for fact in enrichment.get("personal_facts", [])[:5]:
        if not isinstance(fact, str) or not fact.strip():
            continue
        fact = fact.strip()
        keywords = [w for w in re.findall(r'[\u4e00-\u9fff\w]+', fact) if len(w) >= 2]
        if any(kw in original_text for kw in keywords):
            result["personal_facts"].append(fact)

    return result


# ─── 主流程 ─────────────────────────────────────────────────

def collect_files(target_file: str | None = None) -> list[Path]:
    if target_file:
        p = (BASE / target_file) if not Path(target_file).is_absolute() else Path(target_file)
        return [p] if p.exists() else []

    files = []
    for md in sorted(BASE.rglob("*.md")):
        parts = md.relative_to(BASE).parts
        if parts[0] in EXCLUDE_DIRS:
            continue
        if md.name in EXCLUDE_FILES:
            continue
        # Profile 資料不做 LLM enrichment（已有結構化內容）
        if parts[0] == "10_Profile":
            continue
        files.append(md)
    return files


def already_enriched(content: str) -> bool:
    """檢查是否已有 enriched_at 欄位。"""
    return "enriched_at:" in content


def enrich_all(model: str, rebuild: bool, dry_run: bool, target_file: str | None,
               weave_tapestry: bool = True):
    files   = collect_files(target_file)
    total   = len(files)
    skipped = 0
    done    = 0
    errors  = 0

    # 延遲載入 Tapestry（避免在 dry-run 時寫入）
    tapestry_G = None
    if weave_tapestry and not dry_run:
        try:
            from tapestry import load_tapestry, save_tapestry, weave_memory as _weave
            tapestry_G = load_tapestry()
        except ImportError:
            tapestry_G = None

    print(f"[ENRICH] 掃描到 {total} 個記憶檔案，模型：{model}")
    if dry_run:
        print("[ENRICH] DRY-RUN 模式，不實際寫入\n")

    for i, path in enumerate(files, 1):
        content = path.read_text(encoding="utf-8")

        if not rebuild and already_enriched(content):
            skipped += 1
            continue

        raw_fm, fm, body = parse_frontmatter(content)
        title         = fm.get("title", path.stem)
        full_text     = f"{title}\n{body}"
        fname_hint_raw = fm.get("filename_hint", [])
        if isinstance(fname_hint_raw, list):
            fname_hint = fname_hint_raw
        elif fname_hint_raw:
            # 可能被 YAML 解析為字串，嘗試還原
            import ast
            try:
                fname_hint = ast.literal_eval(str(fname_hint_raw))
            except Exception:
                fname_hint = [str(fname_hint_raw)]
        else:
            fname_hint = []

        print(f"[{i}/{total}] {path.relative_to(BASE)} ... ", end="", flush=True)

        try:
            raw_enrichment  = call_llm(title, full_text, model, filename_hint=fname_hint)
            enrichment      = validate_entities(raw_enrichment, full_text)
            rewrite_file_with_enrichment(path, enrichment, dry_run)

            locs   = enrichment["entities"]["locations"]
            period = enrichment.get("period", "")
            facts  = enrichment.get("personal_facts", [])
            print(f"OK  locs={locs}  period={period!r}  facts={len(facts)}")
            done += 1

            # ── 織入 Tapestry ──────────────────────────────
            if tapestry_G is not None:
                rel_path = str(path.relative_to(BASE))
                _weave(tapestry_G, rel_path, enrichment)

        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1

    # ── 儲存 Tapestry ───────────────────────────────────────
    if tapestry_G is not None and done > 0:
        save_tapestry(tapestry_G)
        print(f"[ENRICH] Tapestry 已更新：{tapestry_G.number_of_nodes()} nodes, "
              f"{tapestry_G.number_of_edges()} edges")

    print(f"\n[ENRICH] 完成：{done} 增強，{skipped} 跳過，{errors} 錯誤")


# ─── CLI ────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Memosyne Enrichment Layer")
    ap.add_argument("--model",          default="gemma4:26b", help="Ollama 模型名稱")
    ap.add_argument("--rebuild",        action="store_true",  help="重新增強所有檔案（含已增強）")
    ap.add_argument("--dry-run",        action="store_true",  help="預覽結果，不實際寫入")
    ap.add_argument("--file",           default=None,         help="只處理單一檔案（相對 BASE 路徑）")
    ap.add_argument("--no-tapestry",    action="store_true",  help="跳過 Tapestry 更新")
    ap.add_argument("--weave-tapestry", action="store_true",
                    help="只重建 Tapestry（不重新增強，從現有 enriched_at 記憶讀取）")
    args = ap.parse_args()

    if args.weave_tapestry:
        from tapestry import backfill_from_vault
        print("[ENRICH] The Grand Weaving — rebuilding Tapestry from existing memories...")
        backfill_from_vault(verbose=True)
        return

    enrich_all(
        model          = args.model,
        rebuild        = args.rebuild,
        dry_run        = args.dry_run,
        target_file    = args.file,
        weave_tapestry = not args.no_tapestry,
    )


if __name__ == "__main__":
    main()
