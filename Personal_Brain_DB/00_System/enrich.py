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
import time
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

from artifacts import data_root
BASE       = data_root()
SYSTEM_DIR = Path(__file__).parent

EXCLUDE_DIRS  = {"00_System"}
EXCLUDE_FILES = {"README.md", ".cursorrules"}

# 單次 LLM enrichment 餵入的最大字元數。超過此長度的長檔（如一次性匯入的
# 大型個人 profile）會被切成多段、各自 enrich，再合併結果——避免只看開頭
# 而漏掉後段的 entities / personal_facts（The Weaving must see the whole cloth）。
ENRICH_SEGMENT_CHARS = 3000

# 預設 Oracle 模型：優先 DeepSeek（雲端 reasoning，穩定），本地 gemma 作保底。
# 雲端端點抽風或某段被審查/重置時，自動退回本地，既保完整覆蓋也讓敏感內容留在本地。
DEFAULT_ENRICH_MODEL    = "deepseek:deepseek-v4-pro"
DEFAULT_FALLBACK_MODELS = ["gemma4:26b"]

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
    "chat_category": "",   # 僅 20_AI_Chats 使用：personal / knowledge / mixed
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

    needs_review = enrichment.get("needs_review", [])
    review_line = ""
    if needs_review:
        review_line = f"\nneeds_review: {json.dumps(needs_review, ensure_ascii=False)}"

    cat = enrichment.get("chat_category", "")
    cat_line = f'\nchat_category: "{cat}"' if cat else ""

    enrich_block = f"""
# ── Enrichment（LLM 語意增強，僅含原文出現的實體）──
enriched_at: "{now}"
importance: {imp}
period: "{period}"
themes: {themes}
personal_facts: {facts}
hyqe_questions: []{cat_line}{review_line}
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
   ⚠️ IMPORTANT for AI-chat records: even when the main topic is knowledge/technical,
   if the user mentions their own possessions / experiences / plans / identifiers,
   STILL extract those as personal_facts. Examples:
     - User asks about a product AND reveals they own model "KMN-9503" → extract "我的眼鏡型號是 KMN-9503"
     - User asks about a city AND mentions they lived there in 2025 → extract "我 2025 住過 X"
     - User asks for code help AND reveals their stack is X → extract "我用 X"
   Max 5 items. Each must be a concise statement (under 30 chars). Return [] if none.
8. chat_category: ONLY for AI-chat records (when is_ai_chat=True below). Classify this conversation:
   - "personal"  = about the author's life/emotions/decisions/relationships
   - "knowledge" = pure factual/technical Q&A with no personal stake (e.g. "how does X work", "what is Y")
   - "mixed"     = personal framing but requesting general knowledge
   For non-chat records, return "".
9. Speak ONLY in pure JSON — no preamble, no explanation, no commentary

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
  "personal_facts": ["作者個人生活事實（非客觀知識），最多5條"],
  "chat_category": "personal | knowledge | mixed | <空字串>"
}}

Memory title: {title}
Filename hint（档名關鍵詞，僅供參考 — 必須驗證於正文中才可使用）: {filename_hint}
is_ai_chat: {is_ai_chat}
Memory fragment:
{content}
"""


def _split_for_enrich(content: str, seg_size: int = ENRICH_SEGMENT_CHARS) -> list[str]:
    """把長文切成 ≤seg_size 的段落，優先在標題 / 空行邊界切，避免切斷句子。

    短於 seg_size 時回傳單元素 list（行為與舊版一致）。
    """
    if len(content) <= seg_size:
        return [content]

    # 以「標題行」與「空行分隔的段落」為自然邊界
    blocks = re.split(r'(?=^#{1,6}\s)|\n{2,}', content, flags=re.M)
    segments: list[str] = []
    buf = ""
    for block in blocks:
        if not block:
            continue
        # 單一 block 本身就超長 → 硬切
        if len(block) > seg_size:
            if buf:
                segments.append(buf)
                buf = ""
            for i in range(0, len(block), seg_size):
                segments.append(block[i:i + seg_size])
            continue
        if len(buf) + len(block) + 2 > seg_size:
            segments.append(buf)
            buf = block
        else:
            buf = f"{buf}\n\n{block}" if buf else block
    if buf:
        segments.append(buf)
    return segments


def _merge_enrichments(parts: list[dict]) -> dict:
    """合併多段 enrichment：entities/themes/personal_facts 取聯集去重（保序），
    period/chat_category 取第一個非空，importance 取最高。"""
    importance_rank = {"low": 0, "medium": 1, "high": 2}

    def _union(seqs: list[list]) -> list:
        seen, out = set(), []
        for seq in seqs:
            for item in (seq or []):
                if item not in seen:
                    seen.add(item)
                    out.append(item)
        return out

    merged = {
        "entities": {
            "locations": _union([p.get("entities", {}).get("locations", []) for p in parts]),
            "people":    _union([p.get("entities", {}).get("people", [])    for p in parts]),
            "events":    _union([p.get("entities", {}).get("events", [])    for p in parts]),
            "emotions":  _union([p.get("entities", {}).get("emotions", [])  for p in parts]),
        },
        # 整篇主題上限放寬到 6（單段 prompt 仍限 4）
        "themes":         _union([p.get("themes", []) for p in parts])[:6],
        "period":         next((p.get("period") for p in parts if p.get("period")), ""),
        "importance":     max((p.get("importance", "medium") for p in parts),
                              key=lambda v: importance_rank.get(v, 1)),
        # 大檔通常承載大量個人事實，上限放寬到 20
        "personal_facts": _union([p.get("personal_facts", []) for p in parts])[:20],
        "chat_category":  next((p.get("chat_category") for p in parts if p.get("chat_category")), ""),
    }
    return merged


def _call_llm_once(title: str, content: str, model: str, hint_str: str,
                   is_ai_chat: bool) -> dict:
    """對單一段落呼叫 LLM，回傳 enrichment dict。"""
    from llm_client import chat_text

    prompt = ENRICHMENT_PROMPT.format(
        title=title,
        filename_hint=hint_str,
        is_ai_chat=str(is_ai_chat),
        content=content,
    )

    raw = chat_text(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        think=False,
    ).strip()

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


def _try_model_with_retry(title: str, content: str, model: str, hint_str: str,
                          is_ai_chat: bool, attempts: int) -> dict:
    """單一模型 + 退避重試。耗盡則 raise。"""
    last_err = None
    for n in range(attempts):
        try:
            return _call_llm_once(title, content, model, hint_str, is_ai_chat)
        except Exception as e:
            last_err = e
            if n < attempts - 1:
                wait = min(2 ** (n + 1), 30)   # 2,4,8,16,30,30... 秒，封頂 30s
                print(f"\n    [retry {n + 1}/{attempts - 1}] {type(e).__name__}，{wait}s 後重試 ... ",
                      end="", flush=True)
                time.sleep(wait)
    raise last_err


def _call_llm_resilient(title: str, content: str, model: str, hint_str: str,
                        is_ai_chat: bool, attempts: int = 8,
                        fallback_models: list[str] | None = None) -> dict:
    """Defying Nemesis — 退避重試 + 模型 fallback 鏈。

    先用主模型重試 attempts 次；耗盡後依序試 fallback_models（每個 3 次重試）。
    用途：主雲端端點抽風（或某段被審查/重置）時，自動降級到下一個 provider，
    鏈尾通常放本地 ollama 作最終保底——既保完整覆蓋，敏感內容也能留在本地。"""
    try:
        return _try_model_with_retry(title, content, model, hint_str, is_ai_chat, attempts)
    except Exception as primary_err:
        if not fallback_models:
            raise
        for fb in fallback_models:
            print(f"\n    [fallback] {model} 連續失敗，改用 {fb} 跑這段 ... ",
                  end="", flush=True)
            try:
                return _try_model_with_retry(title, content, fb, hint_str, is_ai_chat, attempts=3)
            except Exception:
                continue
        raise primary_err


def call_llm(title: str, content: str, model: str, filename_hint: list = None,
             is_ai_chat: bool = False, fallback_models: list[str] | None = None) -> dict:
    """呼叫 LLM（Ollama / OpenRouter），回傳 enrichment dict。

    長檔自動分段 enrich 後合併（見 _split_for_enrich / _merge_enrichments），
    確保整篇都被 Oracle 讀過，而非只看前 ENRICH_SEGMENT_CHARS 字。
    每段呼叫帶退避重試；耗盡後依 fallback_models 鏈依序降級。
    """
    hint_str = "、".join(filename_hint) if filename_hint else "（無）"

    segments = _split_for_enrich(content)
    if len(segments) == 1:
        return _call_llm_resilient(title, segments[0], model, hint_str, is_ai_chat,
                                   fallback_models=fallback_models)

    print(f"\n    [分段 enrich] 全文 {len(content)} 字 → {len(segments)} 段 ... ",
          end="", flush=True)
    parts: list[dict] = []
    for idx, seg in enumerate(segments, 1):
        if idx > 1:
            time.sleep(1)   # 段間小停頓，避免被雲端端點當成 burst 而斷線
        parts.append(_call_llm_resilient(title, seg, model, hint_str, is_ai_chat,
                                         fallback_models=fallback_models))
        print(f"{idx}✓ ", end="", flush=True)
    return _merge_enrichments(parts)



# ─── 驗證：只保留原文中出現的實體 ────────────────────────────

def _load_alias_map() -> dict[str, str]:
    """
    從 Tapestry 載入 alias → canonical_name 查找表。
    若 Tapestry 不可用則回傳空 dict（靜默降級）。
    """
    try:
        from tapestry import get_alias_map
        return get_alias_map()
    except Exception:
        return {}


def resolve_person_aliases(enrichment: dict) -> dict:
    """
    The Naming Rite — 即時 alias 偵測。
    將 enrichment 中的 person 名稱替換為 canonical name。
    """
    alias_map = _load_alias_map()
    if not alias_map:
        return enrichment

    people = enrichment.get("entities", {}).get("people", [])
    resolved = []
    for person in people:
        norm = person.lower().replace("-", "").replace("_", "").replace(" ", "").strip()
        canonical = alias_map.get(norm) or alias_map.get(person)
        resolved.append(canonical if canonical else person)

    # 去重（不同別名可能解析到同一 canonical）
    seen = set()
    deduped = []
    for p in resolved:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    enrichment["entities"]["people"] = deduped
    return enrichment

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
        "chat_category":  "",
    }

    # chat_category 白名單（僅接受四個值；其他一律回空）
    raw_cat = enrichment.get("chat_category", "")
    if isinstance(raw_cat, str) and raw_cat.strip().lower() in {"personal", "knowledge", "mixed"}:
        result["chat_category"] = raw_cat.strip().lower()

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


# ─── The Mirror of Truth — Self-RAG 批判 ────────────────────

CRITIQUE_PROMPT = """\
You are the Mirror of Truth, inspector for the Oracle of Mneme.
Given the SOURCE TEXT and a set of CLAIMED FIELDS extracted from it,
rule on each claim: is it directly supported by the text, or a hallucination?

RULES:
1. "supported"  = the claim can be verified by text literally present in the source
                  (exact wording not required, but meaning must be clearly there)
2. "partial"    = loosely implied but not explicit; should be kept but flagged
3. "unsupported"= cannot be found in the text; this is a hallucination — remove

Speak ONLY in pure JSON:

{{
  "personal_facts": [
    {{"text": "<original fact>", "verdict": "supported|partial|unsupported", "reason": "<short>"}}
  ],
  "themes": [
    {{"text": "<theme>", "verdict": "...", "reason": "..."}}
  ],
  "period": {{"text": "<period>", "verdict": "...", "reason": "..."}}
}}

SOURCE TEXT:
{text}

CLAIMED FIELDS:
{claims}
"""


def critique_enrichment(enrichment: dict, original_text: str, model: str) -> dict:
    """
    Self-RAG critique：對 personal_facts / themes / period 做語意批判。
    - unsupported → 移除
    - partial     → 保留但加入 needs_review
    - supported   → 保留
    """
    from llm_client import chat_text

    facts  = list(enrichment.get("personal_facts", []))
    themes = list(enrichment.get("themes", []))
    period = enrichment.get("period", "")

    if not facts and not themes and not period:
        return enrichment

    # 長檔的 enrichment 來自多段合併，但 critique 只能看 text[:3000]；
    # 若在此驗證，後段抽出的 facts 會因「不在前 3000 字」被誤判 unsupported 而刪除。
    # 故長檔跳過 critique（分段 critique 屬另一範疇）。
    if len(original_text) > ENRICH_SEGMENT_CHARS:
        print("  [Critique] 長檔已分段 enrich，跳過 critique（避免誤刪後段 facts）")
        return enrichment

    claims = {
        "personal_facts": facts,
        "themes":         themes,
        "period":         period,
    }

    prompt = CRITIQUE_PROMPT.format(
        text=original_text[:3000],
        claims=json.dumps(claims, ensure_ascii=False, indent=2),
    )

    try:
        raw = chat_text(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            think=False,
        ).strip()
        start = raw.find("{")
        end   = raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON")
        verdict = json.loads(raw[start:end + 1])
    except Exception as e:
        print(f"  [Critique] Mirror faltered: {e}")
        return enrichment

    needs_review: list[str] = []

    def _apply(key: str, items: list[str]) -> list[str]:
        kept = []
        entries = verdict.get(key, [])
        if not isinstance(entries, list):
            return items
        verdict_map = {}
        for e in entries:
            if isinstance(e, dict) and "text" in e:
                verdict_map[str(e["text"])] = str(e.get("verdict", "")).lower()
        for item in items:
            v = verdict_map.get(item, "supported")
            if v == "unsupported":
                continue
            kept.append(item)
            if v == "partial":
                needs_review.append(f"{key}:{item}")
        return kept

    enrichment["personal_facts"] = _apply("personal_facts", facts)
    enrichment["themes"]         = _apply("themes", themes)

    period_entry = verdict.get("period")
    if isinstance(period_entry, dict):
        v = str(period_entry.get("verdict", "")).lower()
        if v == "unsupported":
            enrichment["period"] = ""
        elif v == "partial":
            needs_review.append(f"period:{period}")

    if needs_review:
        enrichment["needs_review"] = needs_review

    return enrichment


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
               weave_tapestry: bool = True, critique: bool = False,
               critique_min_importance: str = "high",
               fallback_models: list[str] | None = None):
    files   = collect_files(target_file)
    total   = len(files)
    skipped = 0
    done    = 0
    errors  = 0

    # 延遲載入 Tapestry（避免在 dry-run 時寫入）
    tapestry_conn = None
    if weave_tapestry and not dry_run:
        try:
            from tapestry import get_conn, weave_memory as _weave
            tapestry_conn = get_conn()
        except ImportError:
            tapestry_conn = None

    # The Deterministic Loom — pure-regex extraction，LLM 前先抽。
    # v0.5：LLM 退到「補強」，frontmatter / body 中已結構化的引用一律保留。
    try:
        from link_extractor import extract_from_content as _det_extract
        from link_extractor import merge_extractions as _merge_ext
    except ImportError:
        _det_extract = None
        _merge_ext = None
    det_added_entities = 0

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
            rel = str(path.relative_to(BASE))
            is_chat = rel.startswith("20_AI_Chats/")
            raw_enrichment  = call_llm(title, full_text, model,
                                       filename_hint=fname_hint, is_ai_chat=is_chat,
                                       fallback_models=fallback_models)
            enrichment      = validate_entities(raw_enrichment, full_text)
            enrichment      = resolve_person_aliases(enrichment)

            # The Mirror of Truth — Self-RAG critique
            importance_rank = {"low": 0, "medium": 1, "high": 2}
            min_rank = importance_rank.get(critique_min_importance, 2)
            this_rank = importance_rank.get(enrichment.get("importance", "medium"), 1)
            if critique and this_rank >= min_rank:
                enrichment = critique_enrichment(enrichment, full_text, model)

            rewrite_file_with_enrichment(path, enrichment, dry_run)

            locs   = enrichment["entities"]["locations"]
            period = enrichment.get("period", "")
            facts  = enrichment.get("personal_facts", [])
            print(f"OK  locs={locs}  period={period!r}  facts={len(facts)}")
            done += 1

            # ── 織入 Tapestry ──────────────────────────────
            # v0.5 The Self-Weaving Tapestry：
            # LLM enrichment + deterministic extraction 取聯集後織入。
            # Frontmatter 仍只寫 LLM 結果（保留 Oracle 的 ground-truth 契約），
            # 但圖譜會吸收 [[wikilink]] / @mention 等 LLM 可能漏抽的訊號。
            if tapestry_conn is not None:
                rel_path = str(path.relative_to(BASE))
                weave_payload = enrichment
                if _det_extract is not None and _merge_ext is not None:
                    try:
                        det = _det_extract(content)
                        # 比對 LLM 沒抽到但 deterministic 抓到的 entity 數
                        llm_ents = (enrichment.get("entities") or {})
                        det_ents = (det.get("entities") or {})
                        for field in ("people", "locations", "events"):
                            new = set(det_ents.get(field, [])) - set(llm_ents.get(field, []))
                            det_added_entities += len(new)
                        # merge_extractions(a, b)：b 蓋 a 的 scalar，entities 聯集。
                        # 把 LLM 當作 b（優先 scalar），deterministic 當作 a。
                        weave_payload = _merge_ext(det, enrichment)
                    except Exception:
                        # 任何抽取失敗都回退到原 enrichment
                        weave_payload = enrichment
                _weave(tapestry_conn, rel_path, weave_payload)

        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1

    if tapestry_conn is not None and done > 0:
        from tapestry import tapestry_stats
        stats = tapestry_stats(tapestry_conn)
        print(f"[ENRICH] Tapestry 已更新：{stats['nodes']} nodes, {stats['edges']} edges")
        if det_added_entities > 0:
            print(f"[ENRICH] The Deterministic Loom 補進 {det_added_entities} 個 LLM 漏抽的 entity 引用")

    print(f"\n[ENRICH] 完成：{done} 增強，{skipped} 跳過，{errors} 錯誤")


# ─── CLI ────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Memosyne Enrichment Layer")
    ap.add_argument("--model",          default=DEFAULT_ENRICH_MODEL,
                    help=f"Oracle 模型（預設 {DEFAULT_ENRICH_MODEL}）")
    ap.add_argument("--rebuild",        action="store_true",  help="重新增強所有檔案（含已增強）")
    ap.add_argument("--dry-run",        action="store_true",  help="預覽結果，不實際寫入")
    ap.add_argument("--file",           default=None,         help="只處理單一檔案（相對 BASE 路徑）")
    ap.add_argument("--no-tapestry",    action="store_true",  help="跳過 Tapestry 更新")
    ap.add_argument("--weave-tapestry", action="store_true",
                    help="只重建 Tapestry（不重新增強，從現有 enriched_at 記憶讀取）")
    ap.add_argument("--critique",       action="store_true",
                    help="The Mirror of Truth — Self-RAG 批判（personal_facts/themes/period）")
    ap.add_argument("--critique-min-importance", default="high",
                    choices=["low", "medium", "high"],
                    help="只對 importance ≥ 此值 的記憶進行批判（預設 high）")
    ap.add_argument("--fallback-model", action="append", default=None, dest="fallback_models",
                    help=f"主模型某段重試耗盡後的降級模型，可重複指定形成鏈（依序嘗試）。"
                         f"未指定時預設 {DEFAULT_FALLBACK_MODELS}；用 --no-fallback 關閉。")
    ap.add_argument("--no-fallback", action="store_true", help="關閉 fallback 鏈")
    args = ap.parse_args()

    # 未指定 --fallback-model 時套用預設鏈；--no-fallback 則清空
    if args.no_fallback:
        args.fallback_models = None
    elif args.fallback_models is None:
        args.fallback_models = list(DEFAULT_FALLBACK_MODELS)

    if args.weave_tapestry:
        from tapestry import backfill_from_vault
        print("[ENRICH] The Grand Weaving — rebuilding Tapestry from existing memories...")
        backfill_from_vault(verbose=True)
        return

    enrich_all(
        model                    = args.model,
        rebuild                  = args.rebuild,
        dry_run                  = args.dry_run,
        target_file              = args.file,
        weave_tapestry           = not args.no_tapestry,
        critique                 = args.critique,
        critique_min_importance  = args.critique_min_importance,
        fallback_models          = args.fallback_models,
    )


if __name__ == "__main__":
    main()
