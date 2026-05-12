#!/usr/bin/env python3
"""
Memosyne — The Deterministic Loom (link_extractor.py)

Zero-LLM 連結抽取：從 markdown 檔案直接抽出 entity references，
不呼叫任何 LLM。靈感來自 gbrain `src/core/link-extraction.ts`，
但 entity 維度對齊 Memosyne 的 Tapestry schema：
Person / Location / Event / Period。

Two sources of truth in priority order:

  1. Frontmatter（canonical）
     - entities.{people,locations,events}: list
     - period: str
     - personal_facts: list[str]

  2. Body patterns（捕網）
     - [[Entity Name]]       wikilink
     - [[type:Entity Name]]  qualified wikilink，type ∈ {person,place,event,period}
     - [Display](path)       markdown link 到 entity 目錄
     - @entity-slug          短記人物（連字號 / 中文）
     - #entity-slug          短記事件
     - 段落內出現在 frontmatter 已宣告 entity 字串的 mention（recall pass）

設計原則：
    - 純函式：輸入文字，輸出結構化 dict。**不寫檔、不連 DB**。
    - 與 Oracle LLM 輸出的 enrichment dict shape 完全相同，可直接傳給 tapestry.weave_memory。
    - 漏抽優於誤抽：未匹配的引用寧可留給 LLM 補；regex 不做語義推斷。

公開介面：

    extract_from_content(content: str) -> dict
        從 markdown 字串抽出 enrichment-shape dict

    extract_from_file(path: Path) -> dict
        從檔案抽（含 source_path 標註）

    merge_extractions(a: dict, b: dict) -> dict
        合併兩個 extraction（聯集 entities，b 蓋 a 的純量欄位）

CLI（自我檢測 / debug 用）：

    python3 link_extractor.py path/to/memory.md
    python3 link_extractor.py --vault path/to/vault --stats
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


# ─── Regex patterns ──────────────────────────────────────────

# Entity dir whitelist。Memosyne 的 entity 不像 gbrain 有 people/ companies/
# 等頂層目錄；entity 散落在 frontmatter。但我們仍保留一條路徑樣式給
# 那些用 [[10_Profile/people/alice]] 風格手動連結的人。
_DIR_WHITELIST = r"(?:10_Profile|20_AI_Chats|30_Journal|40_Projects|50_Knowledge|entities)"

# [Display](path) markdown link
_MD_LINK_RE = re.compile(
    rf"\[([^\]]+)\]\((?:\.\./)*({_DIR_WHITELIST}/[^)\s]+?)(?:\.md)?\)",
)

# [[Entity Name]] — unqualified wikilink（純名稱，無冒號）
_WIKILINK_RE = re.compile(
    r"\[\[([^\]|:#]+?)(?:#[^\]|]+?)?(?:\|([^\]]+?))?\]\]",
)

# [[type:Entity Name]] — qualified wikilink，type 決定 entity 類型
_QUALIFIED_WIKILINK_RE = re.compile(
    r"\[\[(person|place|location|event|period|muse):([^\]|#]+?)(?:#[^\]|]+?)?(?:\|([^\]]+?))?\]\]",
    re.IGNORECASE,
)

# @entity-slug — 中英文皆可，至少兩字
_AT_MENTION_RE = re.compile(
    r"(?:^|[^\w一-鿿])@([A-Za-z一-鿿][\w一-鿿-]{1,40})",
)

# #event-slug — 同上但用 # 開頭。為了避免吃到 markdown heading，
# 要求 # 前面是非空白字元或行內位置。
_HASH_MENTION_RE = re.compile(
    r"(?<=\S)#([A-Za-z一-鿿][\w一-鿿-]{1,40})"
    r"|(?<=[\(（\[])#([A-Za-z一-鿿][\w一-鿿-]{1,40})",
)

# Markdown fenced code block / inline code — 抽取前先 mask 掉
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

# Frontmatter delimiter
_FM_DELIM_RE = re.compile(r"^---\s*\n", re.MULTILINE)


# ─── Type alias 對應到 Tapestry node 類型 ────────────────────

_QUALIFIER_TO_BUCKET = {
    "person": "people",
    "place": "locations",
    "location": "locations",
    "event": "events",
    "period": "periods",   # Period 在 enrichment dict 中是 scalar，特例
    "muse": "people",      # muse 視為 person（暫定）
}


# ─── 主要抽取函式 ────────────────────────────────────────────

def _mask_code(content: str) -> str:
    """把 fenced + inline code 段替換成同長度空白，避免吃到程式碼裡的 #。"""
    def _blank(m: re.Match) -> str:
        return " " * (m.end() - m.start())
    content = _FENCED_CODE_RE.sub(_blank, content)
    content = _INLINE_CODE_RE.sub(_blank, content)
    return content


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """切出 frontmatter dict 與 body。沒有 frontmatter 則回 ({}, content)。"""
    if not content.startswith("---"):
        return {}, content

    # 找第二個 --- 結尾
    end = content.find("\n---", 3)
    if end < 0:
        return {}, content

    raw_fm = content[3:end]
    body = content[end + 4 :].lstrip("\n")

    if yaml is None:
        return {}, body

    # 去掉註解行（# 開頭）— Memosyne enrichment 偶爾有 inline 註解
    clean_lines = [ln for ln in raw_fm.split("\n") if not ln.strip().startswith("#")]
    try:
        fm = yaml.safe_load("\n".join(clean_lines)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, body


def _from_frontmatter(fm: dict) -> dict:
    """從 frontmatter dict 抽出 entities + period + personal_facts。"""
    ents = fm.get("entities") or {}
    if not isinstance(ents, dict):
        ents = {}

    def _list(field: str) -> list[str]:
        raw = ents.get(field)
        if not isinstance(raw, list):
            return []
        return [str(x).strip() for x in raw if isinstance(x, (str, int, float)) and str(x).strip()]

    period = fm.get("period", "") or ""
    if not isinstance(period, str):
        period = str(period)

    facts = fm.get("personal_facts") or []
    if not isinstance(facts, list):
        facts = []
    facts = [str(f).strip() for f in facts if isinstance(f, (str, int, float)) and str(f).strip()]

    themes = fm.get("themes") or []
    if not isinstance(themes, list):
        themes = []
    themes = [str(t).strip() for t in themes if isinstance(t, (str, int, float)) and str(t).strip()]

    return {
        "entities": {
            "locations": _list("locations"),
            "people":    _list("people"),
            "events":    _list("events"),
        },
        "period":         period.strip(),
        "personal_facts": facts,
        "themes":         themes,
    }


def _from_body(body: str, fm_extraction: dict) -> dict:
    """
    從 body 文字抽 entity references。
    fm_extraction 用來做 recall pass：frontmatter 已宣告的 entity
    若在 body 也出現（但非結構化引用），仍會被列入（強化 grounding）。
    """
    body_clean = _mask_code(body)

    buckets: dict[str, set[str]] = {
        "people": set(),
        "locations": set(),
        "events": set(),
        "periods": set(),
    }

    # 1. Qualified wikilinks [[type:name]] — 最高信號
    for m in _QUALIFIED_WIKILINK_RE.finditer(body_clean):
        qualifier = m.group(1).lower()
        name = (m.group(3) or m.group(2)).strip()
        bucket = _QUALIFIER_TO_BUCKET.get(qualifier)
        if bucket and name:
            buckets[bucket].add(name)

    # 2. Unqualified wikilinks [[name]] — 不分配 bucket，留給 recall pass
    #    （因為無類型資訊，貿然歸到 people 會誤抽）
    unqualified_wiki: set[str] = set()
    for m in _WIKILINK_RE.finditer(body_clean):
        # 跳過 qualified（被上一輪吃過）
        text = m.group(0)
        if _QUALIFIED_WIKILINK_RE.fullmatch(text):
            continue
        display = (m.group(2) or m.group(1)).strip()
        if display:
            unqualified_wiki.add(display)

    # 3. Markdown links 到 entity 目錄 — 不分配，留給 recall pass
    unqualified_md: set[str] = set()
    for m in _MD_LINK_RE.finditer(body_clean):
        display = m.group(1).strip()
        if display:
            unqualified_md.add(display)

    # 4. @-mention → people（穩定信號）
    for m in _AT_MENTION_RE.finditer(body_clean):
        name = m.group(1).strip()
        if name and len(name) >= 2:
            buckets["people"].add(name)

    # 5. #-mention → events（次穩信號）
    for m in _HASH_MENTION_RE.finditer(body_clean):
        name = (m.group(1) or m.group(2) or "").strip()
        if name and len(name) >= 2:
            buckets["events"].add(name)

    # 6. Recall pass：frontmatter 宣告的 entity 若在 body 出現，加進對應 bucket。
    #    這保證 frontmatter 與 body 的引用一致性，並讓未來「body 改字、忘記更新 frontmatter」
    #    的情況能被偵測（透過 buckets vs fm 的 diff）。
    for bucket, fm_field in [("people", "people"),
                             ("locations", "locations"),
                             ("events", "events")]:
        for name in fm_extraction.get("entities", {}).get(fm_field, []):
            if name and name in body:
                buckets[bucket].add(name)

    # 7. unqualified_wiki / unqualified_md 中，若名稱與 frontmatter 已宣告的 entity 匹配，
    #    歸入對應 bucket；否則丟棄（避免誤分類）。
    fm_ents = fm_extraction.get("entities", {})
    fm_lookup: dict[str, str] = {}
    for bucket, field in [("people", "people"), ("locations", "locations"), ("events", "events")]:
        for name in fm_ents.get(field, []):
            fm_lookup[name.lower()] = bucket

    for name in unqualified_wiki | unqualified_md:
        bucket = fm_lookup.get(name.lower())
        if bucket:
            buckets[bucket].add(name)

    return {
        "entities": {
            "locations": sorted(buckets["locations"]),
            "people":    sorted(buckets["people"]),
            "events":    sorted(buckets["events"]),
        },
        # 注意：period 只從 frontmatter 來；body 中 [[period:...]] 已合併到 buckets["periods"]，
        # 但 enrichment dict 的 period 是 scalar，我們取第一個（如果有）。
        "_periods_from_body": sorted(buckets["periods"]),
    }


def merge_extractions(a: dict, b: dict) -> dict:
    """
    合併兩個 extraction。entities 取聯集；
    period / personal_facts / themes：以 b 優先（但 b 為空時保留 a）。
    """
    def _union(field: str) -> list[str]:
        sa = set((a.get("entities") or {}).get(field) or [])
        sb = set((b.get("entities") or {}).get(field) or [])
        return sorted(sa | sb)

    def _prefer(field: str):
        bv = b.get(field)
        if bv:  # 非空（非空字串、非空 list）
            return bv
        return a.get(field) or ("" if field == "period" else [])

    return {
        "entities": {
            "locations": _union("locations"),
            "people":    _union("people"),
            "events":    _union("events"),
        },
        "period":         _prefer("period"),
        "personal_facts": _prefer("personal_facts"),
        "themes":         _prefer("themes"),
    }


def extract_from_content(content: str) -> dict:
    """
    從 markdown 字串抽出 enrichment-shape dict。

    Returns:
        {
          "entities": {"locations": [...], "people": [...], "events": [...]},
          "period": str,
          "personal_facts": [...],
          "themes": [...],
          "_source": "deterministic",
          "_extractor_version": "v0.5",
        }
    """
    fm, body = _split_frontmatter(content)
    fm_ext = _from_frontmatter(fm)
    body_ext = _from_body(body, fm_ext)

    # 合併：frontmatter 是 a（弱），body 補強 entities（聯集）。
    # period / personal_facts / themes 只從 frontmatter（body 不會宣告這些 scalar）。
    merged_entities = {
        "locations": sorted(
            set(fm_ext["entities"]["locations"]) | set(body_ext["entities"]["locations"])
        ),
        "people": sorted(
            set(fm_ext["entities"]["people"]) | set(body_ext["entities"]["people"])
        ),
        "events": sorted(
            set(fm_ext["entities"]["events"]) | set(body_ext["entities"]["events"])
        ),
    }

    period = fm_ext["period"]
    # 若 frontmatter 沒給 period 但 body 有 [[period:...]]，取第一個
    if not period and body_ext.get("_periods_from_body"):
        period = body_ext["_periods_from_body"][0]

    return {
        "entities":       merged_entities,
        "period":         period,
        "personal_facts": fm_ext["personal_facts"],
        "themes":         fm_ext["themes"],
        "_source":        "deterministic",
        "_extractor_version": "v0.5",
    }


def extract_from_file(path: Path) -> dict:
    """從檔案抽，回傳結果含 _source_path。"""
    content = path.read_text(encoding="utf-8")
    result = extract_from_content(content)
    result["_source_path"] = str(path)
    return result


# ─── Bulk / stats helpers ────────────────────────────────────

def extract_from_vault(
    vault_root: Path,
    excluded_parts: Iterable[str] = ("00_System", "_vault"),
    excluded_filenames: Iterable[str] = ("README.md", ".cursorrules"),
) -> dict[str, dict]:
    """
    掃描 vault 下所有 .md，回傳 {rel_path: extraction}。

    與 LLM enrichment 無關 — 直接從 frontmatter + body 抽，
    用於 Tapestry 重建、稽核、與 LLM 結果比對。
    """
    excluded_parts = set(excluded_parts)
    excluded_filenames = set(excluded_filenames)

    results: dict[str, dict] = {}
    for md_file in sorted(vault_root.rglob("*.md")):
        if any(part in excluded_parts for part in md_file.parts):
            continue
        if md_file.name in excluded_filenames:
            continue
        try:
            ext = extract_from_file(md_file)
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(md_file.relative_to(vault_root))
        results[rel] = ext
    return results


def stats(extractions: dict[str, dict]) -> dict:
    """彙總 extraction 統計：節點計數、含實體的記憶數等。"""
    locs: set[str] = set()
    people: set[str] = set()
    events: set[str] = set()
    periods: set[str] = set()
    mem_with_entity = 0

    for ext in extractions.values():
        ents = ext.get("entities", {})
        locs |= set(ents.get("locations", []))
        people |= set(ents.get("people", []))
        events |= set(ents.get("events", []))
        if ext.get("period"):
            periods.add(ext["period"])
        if any([ents.get("locations"), ents.get("people"),
                ents.get("events"), ext.get("period")]):
            mem_with_entity += 1

    return {
        "memories":          len(extractions),
        "memories_with_any": mem_with_entity,
        "unique_people":     len(people),
        "unique_locations":  len(locs),
        "unique_events":     len(events),
        "unique_periods":    len(periods),
    }


# ─── CLI ─────────────────────────────────────────────────────

def _main() -> int:
    ap = argparse.ArgumentParser(description="Memosyne deterministic link extractor")
    ap.add_argument("path", nargs="?", help="markdown 檔案路徑")
    ap.add_argument("--vault", help="掃整個 vault 並輸出 stats")
    ap.add_argument("--stats", action="store_true", help="搭配 --vault 印 stats")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    args = ap.parse_args()

    if args.vault:
        vault = Path(args.vault).resolve()
        if not vault.is_dir():
            print(f"vault not found: {vault}", file=sys.stderr)
            return 2
        extractions = extract_from_vault(vault)
        if args.stats or not args.json:
            s = stats(extractions)
            print(f"🜍 The Deterministic Loom — vault scan")
            print(f"   memories scanned:    {s['memories']}")
            print(f"   with ≥1 entity:      {s['memories_with_any']}")
            print(f"   unique people:       {s['unique_people']}")
            print(f"   unique locations:    {s['unique_locations']}")
            print(f"   unique events:       {s['unique_events']}")
            print(f"   unique periods:      {s['unique_periods']}")
        if args.json:
            print(json.dumps(extractions, ensure_ascii=False, indent=2))
        return 0

    if not args.path:
        ap.print_help()
        return 2

    p = Path(args.path)
    if not p.is_file():
        print(f"not a file: {p}", file=sys.stderr)
        return 2

    result = extract_from_file(p)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"🜍 {p}")
        ents = result["entities"]
        print(f"   period:         {result['period']!r}")
        print(f"   people:         {ents['people']}")
        print(f"   locations:      {ents['locations']}")
        print(f"   events:         {ents['events']}")
        print(f"   themes:         {result['themes']}")
        print(f"   personal_facts: {len(result['personal_facts'])} 條")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
