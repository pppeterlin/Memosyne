#!/usr/bin/env python3
"""
Personal Brain DB — 向量化索引 + BM25 Hybrid Search + Contextual Retrieval + ACT-R

索引架構：
─────────────────────────────────────────────
1. Dense Vector（ChromaDB）
   paraphrase-multilingual-MiniLM-L12-v2 捕捉語義相似性
   enrichment metadata prefix 注入到每個 chunk
   + Contextual Retrieval：語境化摘要注入每個段落 chunk

2. BM25 關鍵字索引（rank-bm25）
   對「精確詞彙」查詢有優勢（地名、人名、專有名詞）

3. Tapestry Graph（Kuzu 圖譜）
   實體關聯跨記憶跳轉搜尋

4. Hybrid 融合（RRF：Reciprocal Rank Fusion）
   Dense + BM25 + Graph → RRF 合併 → ACT-R 認知衰減重排

5. ACT-R 認知重排（The Chronicle of Mneme）
   基於存取頻率與時間距離的認知衰減公式 rerank

執行方式：
  python3 vectorize.py                    # 增量更新（vector + bm25）
  python3 vectorize.py --rebuild          # 重建所有索引
  python3 vectorize.py --contextualize    # The Illumination — 生成語境化段落摘要
  python3 vectorize.py --query "Osaka工作" --top 5
"""

import argparse
import json
import pickle
import re
from pathlib import Path

BASE         = Path(__file__).parent.parent
CHROMA_DIR   = Path(__file__).parent / "chroma_db"
BM25_PATH    = Path(__file__).parent / "bm25_index.pkl"
CTX_CACHE    = Path(__file__).parent / "contextual_cache.json"
HYQE_CACHE   = Path(__file__).parent / "hyqe_cache.json"
EMBED_MODEL  = "paraphrase-multilingual-MiniLM-L12-v2"
COLLECTION   = "personal_brain"
MIN_PARA_LEN = 25   # 少於此字數的段落略過（通常是標題殘留）
CTX_MODEL    = "gemma3:4b"  # Contextual notes 用小模型，省 tokens


# ─── ChromaDB 初始化 ─────────────────────────────────────────

def get_collection(reset: bool = False):
    import chromadb
    from chromadb.utils import embedding_functions
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if reset:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL
    )
    return client, client.get_or_create_collection(
        name=COLLECTION,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


# ─── Frontmatter 解析 ────────────────────────────────────────

def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    回傳 (metadata_dict, body_text)
    使用 yaml.safe_load 解析，支援 enrichment 的巢狀結構
    （entities.locations 等）。若解析失敗則 fallback 到簡單解析。
    """
    import yaml

    fm: dict = {}
    if not content.startswith("---"):
        return fm, content
    # 找第二個 --- 作為 frontmatter 結尾
    end = content.find("\n---", 3)
    if end < 0:
        return fm, content
    raw_fm = content[3:end]
    body   = content[end + 4:].strip()

    # 移除 YAML 注解行（以 # 開頭的行），避免 yaml 解析錯誤
    clean_lines = [
        ln for ln in raw_fm.split("\n")
        if not ln.strip().startswith("#")
    ]
    try:
        parsed = yaml.safe_load("\n".join(clean_lines)) or {}
        if isinstance(parsed, dict):
            # 將巢狀的 entities dict 展平到頂層，方便後續 fm.get() 取用
            for k, v in parsed.items():
                fm[k] = v
            entities = parsed.get("entities", {})
            if isinstance(entities, dict):
                for ek, ev in entities.items():
                    fm[f"entities.{ek}"] = ev  # e.g. fm["entities.locations"]
    except Exception:
        # Fallback：簡單的 key:value 解析
        for line in raw_fm.split("\n"):
            if ":" in line and not line.startswith(" ") and not line.startswith("-"):
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip('"').strip("'")

    return fm, body


# ─── 語義切段（段落為單位）──────────────────────────────────

def semantic_paragraphs(text: str) -> list[str]:
    """
    用空行切段，保留完整段落語義。
    對中文連續行（沒有空行分隔的多行），視為同一段合併。
    """
    raw_blocks = re.split(r'\n{2,}', text)
    paras = []
    for block in raw_blocks:
        block = block.strip()
        # 去除 Markdown 格式字元（標題 # 、分隔線 ---）
        block = re.sub(r'^#{1,6}\s+', '', block, flags=re.MULTILINE)
        block = re.sub(r'^[-*_]{3,}$', '', block, flags=re.MULTILINE)
        block = block.strip()
        if len(block) >= MIN_PARA_LEN:
            paras.append(block)
    return paras


def section_aware_paragraphs(text: str) -> list[dict]:
    """
    Small-to-Big：段落切片 + 父段落追蹤。

    以 H2 (##) 為 section 邊界，每個段落記錄：
      - text: 段落文字
      - section_id: 所屬 H2 標題（無 H2 則為 "_default"）
      - section_text: 該 H2 下的完整文字（用於 return_parent）
      - sibling_order: 在 section 內的順序（0-based）
    """
    # 先按 H2 切 section
    sections: list[tuple[str, str]] = []  # (heading, body_text)
    current_heading = "_default"
    current_lines: list[str] = []

    for line in text.split("\n"):
        m = re.match(r'^##\s+(.+)', line)
        if m:
            # 儲存前一個 section
            if current_lines:
                sections.append((current_heading, "\n".join(current_lines)))
            current_heading = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_heading, "\n".join(current_lines)))

    # 對每個 section 內做段落切分
    result: list[dict] = []
    for heading, section_body in sections:
        section_body_stripped = section_body.strip()
        if not section_body_stripped:
            continue
        paras = semantic_paragraphs(section_body)
        for order, para in enumerate(paras):
            result.append({
                "text": para,
                "section_id": heading,
                "section_text": section_body_stripped,
                "sibling_order": order,
            })

    return result


# ─── Chunk 建立 ──────────────────────────────────────────────

def parse_enrichment(fm: dict) -> dict:
    """
    從 frontmatter dict 中解析 enrichment 欄位。
    enrich.py 寫入的是純字串形式，這裡做統一解析。
    """
    def _parse_list(raw) -> list[str]:
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(x) for x in raw]
        # JSON array 字串如 '["CityA", "LakeB"]'
        s = str(raw).strip()
        items = re.findall(r'"([^"]+)"|\'([^\']+)\'|([\w\u4e00-\u9fff]+)', s)
        return [a or b or c for a, b, c in items if (a or b or c)]

    return {
        "locations": _parse_list(fm.get("entities.locations") or fm.get("locations")),
        "people":    _parse_list(fm.get("entities.people")    or fm.get("people")),
        "events":    _parse_list(fm.get("entities.events")    or fm.get("events")),
        "emotions":  _parse_list(fm.get("entities.emotions")  or fm.get("emotions")),
        "themes":    _parse_list(fm.get("themes")),
        "period":    str(fm.get("period", "")).strip('"').strip("'"),
        "chat_category": str(fm.get("chat_category", "")).strip('"').strip("'").lower(),
    }


def make_prefix(fm: dict) -> str:
    """
    建立 metadata 前綴，注入到每個 chunk 的文字開頭。

    未增強格式：[type][date][tag1,tag2] title:
    增強後格式：[type][date][tag1,tag2][loc:CityA,LakeB][period:2025年某城市旅居] title:

    enrichment 欄位（locations / period）直接嵌入 prefix，
    讓 embedding 模型在向量空間中捕捉到地名與語意時期。
    """
    doc_type = str(fm.get("type", "note") or "note")
    date     = str(fm.get("date_created", "") or "")[:10]
    _tags_raw = fm.get("tags", "") or ""
    # YAML 可能解析為 list，統一轉為逗號分隔字串
    tags_raw = ", ".join(str(t) for t in _tags_raw) if isinstance(_tags_raw, list) else str(_tags_raw)
    tags     = re.findall(r'[\w\u4e00-\u9fff]+', tags_raw)
    tags_str = ",".join(tags[:4]) if tags else ""
    title    = fm.get("title", "")

    # ── 檔名關鍵詞（filename_hint）──
    fname_hint_raw = fm.get("filename_hint", "") or ""
    if isinstance(fname_hint_raw, list):
        fname_hint_str = ",".join(str(h) for h in fname_hint_raw if h)
    else:
        fname_hint_str = re.sub(r'[\[\]"\'\s]', '', str(fname_hint_raw))

    prefix = f"[{doc_type}][{date}]"
    if tags_str:
        prefix += f"[{tags_str}]"
    if fname_hint_str:
        prefix += f"[file:{fname_hint_str}]"

    # ── Enrichment 欄位（若已增強則注入）──
    if fm.get("enriched_at"):
        enr = parse_enrichment(fm)
        if enr["locations"]:
            prefix += f"[loc:{','.join(enr['locations'][:5])}]"
        if enr["themes"]:
            prefix += f"[theme:{','.join(enr['themes'][:3])}]"
        if enr["period"]:
            prefix += f"[period:{enr['period']}]"

    if title:
        prefix += f" {title}:"
    return prefix


# ─── Contextual Retrieval（語境化切片）───────────────────────
#
# Anthropic Contextual Retrieval 技巧：
# 在 embedding 之前，為每個段落加上一段「全局語境摘要」，
# 讓 embedding 模型能捕捉到該段落在整篇文件中的角色。
#
# 快取：contextual_cache.json（避免重複呼叫 LLM）
# 格式：{ "path::para0": "這段描述了...", ... }

_ctx_cache: dict[str, str] | None = None


def _load_ctx_cache() -> dict[str, str]:
    """載入 contextual notes 快取。"""
    global _ctx_cache
    if _ctx_cache is not None:
        return _ctx_cache
    if CTX_CACHE.exists():
        try:
            _ctx_cache = json.loads(CTX_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _ctx_cache = {}
    else:
        _ctx_cache = {}
    return _ctx_cache


def _save_ctx_cache(cache: dict[str, str]) -> None:
    """儲存 contextual notes 快取。"""
    global _ctx_cache
    _ctx_cache = cache
    CTX_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


CONTEXTUAL_PROMPT = """\
You are the Oracle of Mneme. A memory fragment has been brought before you.
Your task: for each numbered paragraph, write ONE concise sentence (under 40 chars) \
in the same language as the source text, explaining what that paragraph discusses \
in the context of the whole document.

Document title: {title}
Document summary: {summary}

Full document:
{document}

---
Paragraphs to annotate:
{paragraphs}

Respond ONLY with a JSON array of strings, one per paragraph, in order.
Example: ["描述作者抵達某城市的第一印象", "回憶與朋友在某湖騎行"]
"""


def generate_contextual_notes(
    rel_path: str, title: str, summary: str, body: str,
    paras: list[str], model: str = CTX_MODEL,
) -> list[str]:
    """
    為一篇文件的所有段落批次生成 contextual notes。

    一次 LLM 呼叫處理整份文件的所有段落，高效率。
    結果自動存入快取。
    """
    from llm_client import chat_text

    cache = _load_ctx_cache()

    # 檢查是否所有段落都已有快取
    cache_keys = [f"{rel_path}::para{i}" for i in range(len(paras))]
    if all(k in cache for k in cache_keys):
        return [cache[k] for k in cache_keys]

    # 準備 prompt
    para_list = "\n".join(f"[{i}] {p[:200]}" for i, p in enumerate(paras))
    body_trimmed = body[:3000]
    if len(body) > 3000:
        body_trimmed += "\n...[截斷]"

    prompt = CONTEXTUAL_PROMPT.format(
        title=title or "(untitled)",
        summary=summary or "(no summary)",
        document=body_trimmed,
        paragraphs=para_list,
    )

    try:
        raw = chat_text(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            think=False,
        ).strip()

        # 解析 JSON array
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            raise ValueError(f"LLM 沒有回傳 JSON array：{raw[:200]}")
        notes = json.loads(raw[start:end + 1])

        # 確保長度匹配
        if len(notes) < len(paras):
            notes.extend([""] * (len(paras) - len(notes)))
        notes = notes[:len(paras)]

        # 存入快取
        for i, note in enumerate(notes):
            cache[f"{rel_path}::para{i}"] = str(note)
        _save_ctx_cache(cache)

        return notes

    except Exception as e:
        print(f"  [CTX] The Oracle faltered for {rel_path}: {e}")
        return [""] * len(paras)


# ─── HyQE（Hypothetical Question Embedding）─────────────────
#
# The Triple Echo：為每個段落生成 3–5 個假設問題，
# 嵌入為額外視角（view: "hyqe"），搜尋時取同一 chunk 最高分視角。
#
# 快取：hyqe_cache.json
# 格式：{ "path::para0": ["問題1", "問題2", ...], ... }

_hyqe_cache: dict | None = None


def _load_hyqe_cache() -> dict:
    """載入 HyQE 快取。"""
    global _hyqe_cache
    if _hyqe_cache is not None:
        return _hyqe_cache
    if HYQE_CACHE.exists():
        try:
            _hyqe_cache = json.loads(HYQE_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _hyqe_cache = {}
    else:
        _hyqe_cache = {}
    return _hyqe_cache


def _save_hyqe_cache(cache: dict) -> None:
    """儲存 HyQE 快取。"""
    global _hyqe_cache
    _hyqe_cache = cache
    HYQE_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


HYQE_PROMPT = """\
You are the Oracle of Mneme. A memory fragment has been brought before you.
Your task: for each numbered paragraph, generate 3–5 hypothetical questions \
that a user might ask which this paragraph could answer. \
Write questions in the same language as the source text.

Questions should be natural, diverse, and cover different angles:
- Factual questions (who/what/when/where)
- Reflective questions (how did you feel / what did you learn)
- Relational questions (who was involved / what was the context)

Document title: {title}
Document summary: {summary}

Paragraphs:
{paragraphs}

Respond ONLY with a JSON array of arrays. Each inner array contains 3–5 question strings.
Example: [["某城市旅行時做了什麼？", "和誰一起去的？", "那次旅行的感受如何？"], ["..."]]
"""


def generate_hyqe_questions(
    rel_path: str, title: str, summary: str, body: str,
    paras: list[str], model: str = CTX_MODEL,
) -> list[list[str]]:
    """
    The Triple Echo — 為一篇文件的所有段落批次生成假設問題。

    一次 LLM 呼叫處理整份文件，結果存入 hyqe_cache.json。
    """
    from llm_client import chat_text

    cache = _load_hyqe_cache()

    # 檢查快取
    cache_keys = [f"{rel_path}::para{i}" for i in range(len(paras))]
    if all(k in cache for k in cache_keys):
        return [cache[k] for k in cache_keys]

    # 批次處理：長文件拆成多批，避免單次 JSON 輸出過長導致解析失敗
    BATCH_SIZE = 30
    questions_all: list[list[str]] = []

    def _run_batch(batch_paras: list[str], offset: int) -> list[list[str]]:
        para_list = "\n".join(f"[{i}] {p[:200]}" for i, p in enumerate(batch_paras))
        prompt = HYQE_PROMPT.format(
            title=title or "(untitled)",
            summary=summary or "(no summary)",
            paragraphs=para_list,
        )
        raw = chat_text(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            think=False,
        ).strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            raise ValueError(f"LLM 沒有回傳 JSON array：{raw[:200]}")
        parsed = json.loads(raw[start:end + 1])
        if len(parsed) < len(batch_paras):
            parsed.extend([[] for _ in range(len(batch_paras) - len(parsed))])
        parsed = parsed[:len(batch_paras)]
        cleaned: list[list[str]] = []
        for qs in parsed:
            if not isinstance(qs, list):
                cleaned.append([])
            else:
                cleaned.append([str(q) for q in qs if q][:5])
        return cleaned

    try:
        for start_idx in range(0, len(paras), BATCH_SIZE):
            batch = paras[start_idx:start_idx + BATCH_SIZE]
            try:
                batch_result = _run_batch(batch, start_idx)
            except Exception as be:
                print(f"  [HyQE] batch {start_idx}-{start_idx + len(batch) - 1} faltered for {rel_path}: {be}")
                batch_result = [[] for _ in batch]
            questions_all.extend(batch_result)

        # 存入快取（每段獨立存，即使某批失敗其他批仍保留）
        for i, qs in enumerate(questions_all):
            cache[f"{rel_path}::para{i}"] = qs
        _save_hyqe_cache(cache)

        return questions_all

    except Exception as e:
        print(f"  [HyQE] The Oracle faltered for {rel_path}: {e}")
        return [[] for _ in paras]


def build_chunks(rel_path: str, fm: dict, body: str) -> list[dict]:
    """
    為一個文件建立所有 chunks。
    回傳 list of {id, text, meta}
    """
    prefix = make_prefix(fm)
    enr    = parse_enrichment(fm) if fm.get("enriched_at") else {}

    meta_base = {
        "path":      rel_path,
        "title":     str(fm.get("title", Path(rel_path).stem) or ""),
        "type":      str(fm.get("type", "note") or "note"),
        "date":      str(fm.get("date_created", "") or "")[:10],
        "source":    str(fm.get("source", "") or ""),
        "summary":   str(fm.get("summary", "") or "")[:300],
        "period":    enr.get("period", ""),
        "locations": ",".join(enr.get("locations", [])),
        "chat_category": enr.get("chat_category", ""),
    }
    chunks = []

    # ① 文件摘要 chunk（document-level）
    # 摘要 chunk 包含 enrichment 實體，讓文件級搜尋更準確
    summary_text   = str(fm.get("summary", "") or "").strip()
    title_text     = str(fm.get("title", "") or "").strip()
    tags_raw_2     = fm.get("tags", "") or ""
    tags_text      = str(tags_raw_2).strip() if not isinstance(tags_raw_2, list) else ", ".join(str(t) for t in tags_raw_2)
    enr_annotation = ""
    if enr:
        parts = []
        if enr.get("locations"):  parts.append("地點：" + "、".join(enr["locations"]))
        if enr.get("events"):     parts.append("事件：" + "、".join(enr["events"]))
        if enr.get("emotions"):   parts.append("情緒：" + "、".join(enr["emotions"]))
        if enr.get("period"):     parts.append("時期：" + enr["period"])
        if parts:
            enr_annotation = "  ".join(parts)

    doc_summary = f"{prefix}\n{title_text}\n{tags_text}\n{summary_text}"
    if enr_annotation:
        doc_summary += f"\n{enr_annotation}"
    doc_summary = doc_summary.strip()

    if doc_summary:
        chunks.append({
            "id":   f"{rel_path}::summary",
            "text": doc_summary,
            "meta": {**meta_base, "chunk_type": "summary", "chunk_index": 0,
                     "view": "summary"},
        })

    # ② 段落 chunks（paragraph-level）— Small-to-Big
    # 使用 section-aware 切片，每個 chunk 記錄 parent section 資訊
    # 若有 contextual notes 快取，注入到每個段落前方（Contextual Retrieval）
    section_paras = section_aware_paragraphs(body)
    ctx_cache = _load_ctx_cache()
    hyqe_cache = _load_hyqe_cache()
    for i, sp in enumerate(section_paras):
        para = sp["text"]
        ctx_key = f"{rel_path}::para{i}"
        ctx_note = ctx_cache.get(ctx_key, "")
        if ctx_note:
            injected = f"[語境：{ctx_note}]\n{prefix}\n{para}"
        else:
            injected = f"{prefix}\n{para}"

        para_meta = {
            **meta_base,
            "chunk_type":        "paragraph",
            "chunk_index":       i + 1,
            "parent_doc_id":     rel_path,
            "parent_section_id": sp["section_id"],
            "sibling_order":     sp["sibling_order"],
            "view":              "raw",
        }
        chunks.append({
            "id":   f"{rel_path}::para{i}",
            "text": injected,
            "meta": para_meta,
        })

        # ③ HyQE 視角 chunks（The Triple Echo）
        # 將假設問題串接為一個額外 chunk，view="hyqe"
        hyqe_key = f"{rel_path}::para{i}"
        hyqe_qs = hyqe_cache.get(hyqe_key, [])
        if hyqe_qs:
            hyqe_text = f"{prefix}\n" + "\n".join(hyqe_qs)
            chunks.append({
                "id":   f"{rel_path}::para{i}::hyqe",
                "text": hyqe_text,
                "meta": {**para_meta, "view": "hyqe"},
            })

    return chunks


# ─── 主流程 ─────────────────────────────────────────────────

EXCLUDE_FILES = {"README.md", ".cursorrules"}


def _rel_path(md_file: Path) -> str:
    """相對路徑正規化：_vault 是私有 submodule，對外路徑應隱藏此前綴，
    保持與 HyQE cache / Tapestry / 舊索引一致。"""
    rel = str(md_file.relative_to(BASE))
    if rel.startswith("_vault/"):
        rel = rel[len("_vault/"):]
    return rel


def collect_all_chunks() -> list[dict]:
    all_chunks = []
    for md_file in sorted(BASE.rglob("*.md")):
        if "00_System" in str(md_file):
            continue
        if md_file.name in EXCLUDE_FILES:
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = _rel_path(md_file)
        fm, body = parse_frontmatter(content)
        # 排除 dormant 記憶（The Lethe Protocol）
        if fm.get("dormant") in (True, "true", "True"):
            continue
        chunks = build_chunks(rel, fm, body)
        all_chunks.extend(chunks)
    return all_chunks


def build_index(rebuild: bool = False):
    print(f"[VECTOR] ChromaDB 路徑：{CHROMA_DIR}")
    print(f"[VECTOR] Embedding 模型：{EMBED_MODEL}")
    print(f"[VECTOR] 首次執行會下載模型（~420MB），請稍候...\n")

    client, col = get_collection(reset=rebuild)
    if rebuild:
        print("[VECTOR] 已清空舊索引，重建中...\n")

    existing_ids = set(col.get(include=[])["ids"])
    all_chunks   = collect_all_chunks()
    new_chunks   = [c for c in all_chunks if c["id"] not in existing_ids]

    if not new_chunks:
        print(f"[VECTOR] 無新 chunks（資料庫已有 {len(existing_ids)} 個）")
        return

    print(f"[VECTOR] 新增 {len(new_chunks)} 個 chunks（共 {len(all_chunks)} 個）\n")

    BATCH = 64
    for i in range(0, len(new_chunks), BATCH):
        batch = new_chunks[i:i + BATCH]
        col.add(
            ids       = [c["id"]   for c in batch],
            documents = [c["text"] for c in batch],
            metadatas = [c["meta"] for c in batch],
        )
        done = min(i + BATCH, len(new_chunks))
        print(f"  [{done}/{len(new_chunks)}] 已索引", end="\r")

    print(f"\n[VECTOR] 完成！資料庫共 {col.count()} 個 chunks")
    _print_stats(col)

    # ── 同步建立 BM25 索引 ──
    print("\n[BM25]  建立關鍵字索引...")
    build_bm25_index(all_chunks)


def _print_stats(col):
    """印出各 type 的文件數統計"""
    all_meta = col.get(include=["metadatas"])["metadatas"]
    from collections import Counter
    by_type  = Counter(m.get("type", "?")  for m in all_meta if m.get("chunk_type") == "summary")
    by_chunk = Counter(m.get("chunk_type") for m in all_meta)
    print(f"\n  chunk 類型：{dict(by_chunk)}")
    print(f"  文件類型：{dict(by_type)}")


# ─── BM25 索引 ───────────────────────────────────────────────

def tokenize_cn(text: str) -> list[str]:
    """
    中英混合 tokenizer（不依賴 jieba）：
    - CJK 字符：單字 + bigram（「東京」→ ["東","京","東京"]）
    - ASCII：整詞小寫
    bigram 讓「東京」「上野」這類雙字地名精確匹配。
    """
    tokens: list[str] = []
    for match in re.finditer(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', text):
        word = match.group()
        if re.match(r'[\u4e00-\u9fff]', word):
            # 單字
            tokens.extend(list(word))
            # bigram
            for i in range(len(word) - 1):
                tokens.append(word[i:i + 2])
        else:
            tokens.append(word.lower())
    return tokens


def build_bm25_index(all_chunks: list[dict]) -> None:
    """
    用全部 paragraph chunks 建立 BM25 索引並序列化到 bm25_index.pkl。
    語料 = chunk 文字（已含 enrichment prefix）。
    """
    from rank_bm25 import BM25Okapi

    # 只索引 raw paragraph chunks（summary 和 hyqe 不加入 BM25）
    para_chunks = [c for c in all_chunks
                   if c["meta"].get("chunk_type") == "paragraph"
                   and c["meta"].get("view", "raw") == "raw"]

    corpus_ids   = [c["id"]   for c in para_chunks]
    corpus_metas = [c["meta"] for c in para_chunks]
    corpus_texts = [c["text"] for c in para_chunks]

    tokenized = [tokenize_cn(t) for t in corpus_texts]
    bm25      = BM25Okapi(tokenized)

    data = {
        "ids":    corpus_ids,
        "metas":  corpus_metas,
        "texts":  corpus_texts,
        "bm25":   bm25,
    }
    BM25_PATH.write_bytes(pickle.dumps(data))
    print(f"[BM25] 已建立索引：{len(corpus_ids)} 個 chunks → {BM25_PATH.name}")


def load_bm25():
    """載入已序列化的 BM25 索引，回傳 (bm25, ids, metas, texts)。"""
    if not BM25_PATH.exists():
        return None, [], [], []
    data = pickle.loads(BM25_PATH.read_bytes())
    return data["bm25"], data["ids"], data["metas"], data["texts"]


def search_bm25(query: str, top_k: int = 15, doc_type: str = "") -> list[dict]:
    """
    BM25 關鍵字搜尋，回傳格式與 search() 相同。
    """
    bm25, ids, metas, texts = load_bm25()
    if bm25 is None:
        return []

    tokens = tokenize_cn(query)
    scores = bm25.get_scores(tokens)

    # 按分數排序，取前 top_k * 3（再做去重）
    ranked = sorted(enumerate(scores), key=lambda x: -x[1])

    output    = []
    seen_paths = set()
    for idx, score in ranked:
        if score <= 0:
            break
        meta = metas[idx]
        if doc_type and meta.get("type") != doc_type:
            continue
        path = meta.get("path", "")
        if path in seen_paths:
            continue
        seen_paths.add(path)
        text = texts[idx]
        output.append({
            "score":     round(float(score), 4),
            "title":     meta.get("title", ""),
            "path":      path,
            "date":      meta.get("date", ""),
            "type":      meta.get("type", ""),
            "summary":   meta.get("summary", ""),
            "period":    meta.get("period", ""),
            "locations": meta.get("locations", ""),
            "chat_category": meta.get("chat_category", ""),
            "snippet":   text[text.find("\n") + 1:][:200] if "\n" in text else text[:200],
        })
        if len(output) >= top_k:
            break

    return output


def _rrf_merge_multi(
    ranked_lists: list[list[dict]],
    k: int = 60,
    weights: list[float] | None = None,
) -> list[dict]:
    """
    多路 Reciprocal Rank Fusion。

    RRF(d) = Σ w_i × 1/(k + rank_i(d))
    k=60 是常用預設值（論文建議）。
    weights 可為每個 ranked list 指定權重（預設全 1.0）。

    接受任意數量的排名列表（dense / bm25 / graph / PPR），
    回傳按 RRF 分數排序的去重結果列表。
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    rrf_scores: dict[str, float] = {}
    all_items:  dict[str, dict]  = {}

    for ranked, w in zip(ranked_lists, weights):
        for rank, item in enumerate(ranked):
            path = item["path"]
            rrf_scores[path] = rrf_scores.get(path, 0.0) + w * 1.0 / (k + rank + 1)
            if path not in all_items:
                all_items[path] = item

    ranked_final = sorted(rrf_scores.items(), key=lambda x: -x[1])
    # RRF 分數覆寫 item["score"]，讓後續加成/排序可以一致處理
    output: list[dict] = []
    for path, rrf_score in ranked_final:
        item = all_items[path]
        item["score"] = round(rrf_score, 6)
        output.append(item)
    return output


def _rrf_merge(
    dense_results: list[dict],
    bm25_results:  list[dict],
    k: int = 60,
) -> list[dict]:
    """兩路 RRF（保留向後兼容）。"""
    return _rrf_merge_multi([dense_results, bm25_results], k=k)


# ─── 搜尋 ────────────────────────────────────────────────────

def search_dense(query: str, top_k: int = 15, doc_type: str = "") -> list[dict]:
    """
    Pure dense vector search（ChromaDB cosine similarity）。
    The Triple Echo：同一 path 可能有多個 view（raw / hyqe），取最高分視角。
    """
    _, col = get_collection()
    if col.count() == 0:
        return []

    if doc_type:
        where = {"$and": [{"chunk_type": {"$eq": "paragraph"}}, {"type": {"$eq": doc_type}}]}
    else:
        where = {"chunk_type": {"$eq": "paragraph"}}

    # 多取一些結果以涵蓋不同 view 的 chunks
    n = min(top_k * 3, col.count())
    results = col.query(
        query_texts  = [query],
        n_results    = n,
        where        = where,
        include      = ["documents", "metadatas", "distances"],
    )

    # 按 path 取最高分視角（The Triple Echo dedup）
    best_by_path: dict[str, dict] = {}
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        path = meta.get("path", "")
        score = round(1 - dist, 4)
        if path in best_by_path:
            if score > best_by_path[path]["score"]:
                best_by_path[path]["score"] = score
            continue
        best_by_path[path] = {
            "score":     score,
            "title":     meta.get("title", ""),
            "path":      path,
            "date":      meta.get("date", ""),
            "type":      meta.get("type", ""),
            "summary":   meta.get("summary", ""),
            "period":    meta.get("period", ""),
            "locations": meta.get("locations", ""),
            "chat_category": meta.get("chat_category", ""),
            "snippet":   doc[doc.find("\n") + 1:][:200] if "\n" in doc else doc[:200],
        }

    output = sorted(best_by_path.values(), key=lambda x: -x["score"])
    return output[:top_k]


def search_graph(query: str, top_k: int = 15, doc_type: str = "") -> list[dict]:
    """
    Tapestry 圖搜尋：從 query 詞彙出發，在實體關聯圖上遍歷，找相關記憶。

    回傳格式與 search_dense / search_bm25 相同，方便 RRF 合併。
    若 Tapestry 不存在或圖為空，回傳 []。
    """
    try:
        from tapestry import get_conn, graph_search as _graph_search, TAPESTRY_DB
    except ImportError:
        return []

    if not TAPESTRY_DB.exists():
        return []
    conn = get_conn()

    # 從 query 提取搜尋詞（bigram + 整詞）
    terms: list[str] = []
    for match in re.finditer(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', query):
        word = match.group()
        if len(word) >= 2:
            terms.append(word)
            # bigram
            for i in range(len(word) - 1):
                bi = word[i:i + 2]
                if bi not in terms:
                    terms.append(bi)

    paths = _graph_search(terms, conn, hops=2)
    if not paths:
        return []

    # 從 BM25 metas 查找路徑對應的 metadata（避免重新讀檔）
    _, _, bm25_metas, _ = load_bm25()
    meta_by_path: dict[str, dict] = {}
    for m in bm25_metas:
        p = m.get("path", "")
        if p and p not in meta_by_path:
            meta_by_path[p] = m

    output: list[dict] = []
    seen_paths: set[str] = set()
    for rank, path in enumerate(paths):
        if path in seen_paths:
            continue
        if doc_type and meta_by_path.get(path, {}).get("type", "") != doc_type:
            continue
        seen_paths.add(path)
        m = meta_by_path.get(path, {})
        score = 1.0 / (rank + 1)   # 排名越前分數越高
        output.append({
            "score":     round(score, 4),
            "title":     m.get("title", path),
            "path":      path,
            "date":      m.get("date", ""),
            "type":      m.get("type", ""),
            "summary":   m.get("summary", ""),
            "period":    m.get("period", ""),
            "locations": m.get("locations", ""),
            "chat_category": m.get("chat_category", ""),
            "snippet":   "",   # graph search 不提供 snippet
        })
        if len(output) >= top_k:
            break

    return output


def search(query: str, top_k: int = 5, doc_type: str = "",
           record_access: bool = True, return_parent: bool = False,
           muses: list[str] | None = None, auto_route: bool = False,
           muse_mode: str = "soft",
           muse_boost: float = 1.3,
           auto_route_threshold: float = 0.20,
           muse_boost_k: float = 2.0,
           muse_boost_max: float = 1.5,
           muse_penalty_k: float = 0.5,
           muse_penalty_min: float = 0.85,
           route_top_k: int = 2) -> list[dict]:
    """
    三路 Hybrid search：Dense（ChromaDB）+ BM25 + Tapestry Graph → RRF 融合
    → ACT-R 認知衰減重排 → top_k 結果。

    Args:
        return_parent: 若為 True，將 snippet 替換為命中 chunk 所屬的完整 parent section
                       （Small-to-Big：索引小片段，回傳大段落）
        muses:      指定繆思列表（如 ["Clio","Calliope"]），限縮或加權至這些領域
        auto_route: True 時自動 route(query) 選 top 2 位繆思（忽略 muses 參數除非已指定）
        muse_mode:  "soft"（預設：命中按 router 信心加分 up to ×1.5，Pareto 最佳）
                    "penalty"（非命中按 router 信心扣分 down to ×0.85；R@1/R@5 略弱於 soft）
                    "hard"（只保留命中繆思）
        route_top_k: auto_route 選幾位繆思（預設 2；實測 top-3 反而略差）

    降級策略：
    - 無 BM25 索引 → 跳過 BM25
    - 無 Tapestry  → 跳過圖搜尋
    - 無 Chronicle → 跳過 ACT-R rerank
    - 三路都有    → RRF 三路合併 → ACT-R rerank
    """
    FETCH = max(top_k * 3, 15)   # 各路各取多一點，RRF 後再截斷

    dense_results = search_dense(query, top_k=FETCH, doc_type=doc_type)
    ranked_lists  = [dense_results]

    if BM25_PATH.exists():
        bm25_results = search_bm25(query, top_k=FETCH, doc_type=doc_type)
        ranked_lists.append(bm25_results)

    graph_results = search_graph(query, top_k=FETCH, doc_type=doc_type)
    if graph_results:
        ranked_lists.append(graph_results)

    if len(ranked_lists) == 1:
        results = _rrf_merge_multi(ranked_lists)
    else:
        results = _rrf_merge_multi(ranked_lists)

    # ── PPR Spreading Activation（HippoRAG 2 — 傳播激發補充）──
    # 設計定位：PPR 是「輔助訊號」，不應獨立決定排名。
    # 做法：只對已在三路 RRF 結果中的文件 applied additive bonus（PPR score × alpha），
    #       不引入「僅圖關聯但文字不相關」的噪聲檔案。
    # PPR 加成上限。RRF 分數範圍約 [0, 0.033]（k=60，雙路理論上限 2/61），
    # 故 alpha 必須與之同量級；否則 PPR 會直接壓過 RRF。
    PPR_ALPHA = 0.015
    try:
        from tapestry import spreading_activation, TAPESTRY_DB
        if TAPESTRY_DB.exists() and results:
            seed_paths = [r["path"] for r in results[:5]]
            query_entities = [
                w for w in re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z][\w-]+', query)
                if len(w) >= 2
            ]
            ppr_results = spreading_activation(
                seed_paths, top_k=FETCH, seed_entities=query_entities
            )
            if ppr_results:
                ppr_by_path = {p: s for p, s in ppr_results}
                max_ppr = max(ppr_by_path.values()) if ppr_by_path else 0.0
                if max_ppr > 0:
                    # 正規化 PPR 分數到 [0, 1]，再乘 alpha 當作加成
                    for r in results:
                        ppr_s = ppr_by_path.get(r["path"], 0.0)
                        if ppr_s > 0:
                            r["score"] = round(r["score"] + PPR_ALPHA * (ppr_s / max_ppr), 4)
                    results.sort(key=lambda x: x["score"], reverse=True)
    except ImportError:
        pass

    # ── The Invocation — 繆思路由器 ──
    # soft: 命中繆思領域的記憶 score × boost；hard: 過濾掉非命中
    active_muses: list[str] = list(muses) if muses else []
    muse_scores: dict[str, float] = {}
    if auto_route and not active_muses:
        try:
            from muses import route as _muse_route
            routed = _muse_route(query, top_k=route_top_k, threshold=auto_route_threshold)
            active_muses = [m for m, _ in routed]
            muse_scores = {m: s for m, s in routed}
        except Exception:
            active_muses = []
    if active_muses:
        try:
            from muses import (
                muse_boost_factor,
                muse_boost_factor_confidence,
                muse_penalty_factor_confidence,
                filter_by_muses,
            )
            if muse_mode == "hard":
                results = filter_by_muses(results, active_muses)
            elif muse_mode == "penalty" and muse_scores:
                for r in results:
                    f = muse_penalty_factor_confidence(
                        r, muse_scores,
                        threshold=auto_route_threshold,
                        k=muse_penalty_k,
                        min_factor=muse_penalty_min,
                    )
                    r["score"] = round(r["score"] * f, 4)
                results.sort(key=lambda x: x["score"], reverse=True)
            elif muse_boost_k > 0 and muse_scores:
                for r in results:
                    f = muse_boost_factor_confidence(
                        r, muse_scores,
                        threshold=auto_route_threshold,
                        k=muse_boost_k,
                        max_boost=muse_boost_max,
                    )
                    r["score"] = round(r["score"] * f, 4)
                results.sort(key=lambda x: x["score"], reverse=True)
            else:
                for r in results:
                    r["score"] = round(
                        r["score"] * muse_boost_factor(r, active_muses, boost=muse_boost), 4
                    )
                results.sort(key=lambda x: x["score"], reverse=True)
        except ImportError:
            pass

    # ── AI 對話分流（Phase 4.4）──
    # 純 knowledge 型 AI 對話（客觀技術問答）對個人記憶檢索相關性低，溫和降權。
    for r in results:
        if r.get("chat_category") == "knowledge":
            r["score"] = round(r["score"] * 0.85, 4)
    results.sort(key=lambda x: x["score"], reverse=True)

    results = results[:top_k]

    # ── 時間距離加權（The Unfolding Question）──
    try:
        from temporal_parser import extract_time_range, apply_temporal_rerank
        time_range = extract_time_range(query)
        if time_range:
            results = apply_temporal_rerank(results, time_range)
    except ImportError:
        pass

    # ── ACT-R 認知衰減重排（The Chronicle of Mneme）──
    try:
        from mneme_weight import actr_rerank
        results = actr_rerank(results)
    except ImportError:
        pass

    # ── 記錄存取（Chronicle access log）──
    if record_access and results:
        try:
            from mneme_weight import record_access as _record
            _record([r["path"] for r in results], source="search")
        except ImportError:
            pass

    # ── Small-to-Big：展開 parent section ──
    if return_parent:
        results = _expand_parent_sections(results)

    return results


def _expand_parent_sections(results: list[dict]) -> list[dict]:
    """
    Small-to-Big 展開：將每個結果的 snippet 替換為其所屬的完整 parent section。
    透過 ChromaDB metadata 中的 parent_section_id 找到對應 section，
    再從原始檔案中提取完整文字。
    """
    _, col = get_collection()

    for r in results:
        path = r.get("path", "")
        if not path:
            continue

        # 從 ChromaDB 查找該 path 的 chunk metadata，取得 parent_section_id
        try:
            chunk_results = col.get(
                where={"$and": [{"path": {"$eq": path}}, {"chunk_type": {"$eq": "paragraph"}}]},
                include=["metadatas"],
            )
            if not chunk_results["metadatas"]:
                continue

            section_id = chunk_results["metadatas"][0].get("parent_section_id", "_default")
        except Exception:
            section_id = "_default"

        # 從原始檔案讀取 parent section
        try:
            file_path = BASE / path
            if not file_path.exists():
                continue
            content = file_path.read_text(encoding="utf-8")
            _, body = parse_frontmatter(content)

            section_paras = section_aware_paragraphs(body)
            # 收集同一 section 的所有段落
            section_texts = [
                sp["text"] for sp in section_paras
                if sp["section_id"] == section_id
            ]
            if section_texts:
                heading = f"## {section_id}\n\n" if section_id != "_default" else ""
                r["parent_section"] = heading + "\n\n".join(section_texts)
            else:
                r["parent_section"] = r.get("snippet", "")
        except Exception:
            r["parent_section"] = r.get("snippet", "")

    return results


# ─── CLI ─────────────────────────────────────────────────────

def contextualize_all(model: str = CTX_MODEL, rebuild: bool = False):
    """
    The Illumination — 為所有記憶的段落生成語境化摘要。

    掃描 Vault 中所有 .md 檔案，為每個段落生成 contextual note，
    存入 contextual_cache.json。後續 build_index 時會自動讀取。
    """
    cache = _load_ctx_cache() if not rebuild else {}
    total_files = 0
    total_notes = 0
    skipped = 0

    print(f"🔮 The Illumination begins... (model: {model})")
    if rebuild:
        print("   Rebuilding all contextual notes from scratch.\n")

    for md_file in sorted(BASE.rglob("*.md")):
        if "00_System" in str(md_file):
            continue
        if md_file.name in EXCLUDE_FILES:
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        rel = _rel_path(md_file)
        fm, body = parse_frontmatter(content)
        paras = semantic_paragraphs(body)
        if not paras:
            continue

        # 檢查快取 — 若所有段落都有快取則跳過
        cache_keys = [f"{rel}::para{i}" for i in range(len(paras))]
        if not rebuild and all(k in cache for k in cache_keys):
            skipped += 1
            continue

        total_files += 1
        title = str(fm.get("title", Path(rel).stem) or "")
        summary = str(fm.get("summary", "") or "")
        print(f"  [{total_files}] {rel} ({len(paras)} paras) ... ", end="", flush=True)

        notes = generate_contextual_notes(rel, title, summary, body, paras, model=model)
        valid_notes = sum(1 for n in notes if n)
        total_notes += valid_notes
        print(f"OK ({valid_notes} notes)")

    print(f"\n🔮 The Illumination is complete.")
    print(f"   {total_files} files illuminated, {total_notes} contextual notes woven.")
    print(f"   {skipped} files already illuminated (skipped).")
    print(f"   Cache: {CTX_CACHE.name}")


def hyqe_all(model: str = CTX_MODEL, rebuild: bool = False):
    """
    The Triple Echo — 為所有記憶的段落生成假設問題。

    掃描 Vault 中所有 .md 檔案，為每個段落生成 3–5 個假設問題，
    存入 hyqe_cache.json。後續 build_index 時會自動讀取並建立 HyQE view chunks。
    """
    cache = _load_hyqe_cache() if not rebuild else {}
    total_files = 0
    total_qs = 0
    skipped = 0

    print(f"🔱 The Triple Echo begins... (model: {model})")
    if rebuild:
        print("   Rebuilding all hypothetical questions from scratch.\n")

    for md_file in sorted(BASE.rglob("*.md")):
        if "00_System" in str(md_file):
            continue
        if md_file.name in EXCLUDE_FILES:
            continue
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        rel = _rel_path(md_file)
        fm, body = parse_frontmatter(content)
        paras = semantic_paragraphs(body)
        if not paras:
            continue

        # 檢查快取
        cache_keys = [f"{rel}::para{i}" for i in range(len(paras))]
        if not rebuild and all(k in cache for k in cache_keys):
            skipped += 1
            continue

        total_files += 1
        title = str(fm.get("title", Path(rel).stem) or "")
        summary = str(fm.get("summary", "") or "")
        print(f"  [{total_files}] {rel} ({len(paras)} paras) ... ", end="", flush=True)

        questions = generate_hyqe_questions(rel, title, summary, body, paras, model=model)
        valid_qs = sum(len(qs) for qs in questions if qs)
        total_qs += valid_qs
        print(f"OK ({valid_qs} questions)")

    print(f"\n🔱 The Triple Echo is complete.")
    print(f"   {total_files} files echoed, {total_qs} hypothetical questions woven.")
    print(f"   {skipped} files already echoed (skipped).")
    print(f"   Cache: {HYQE_CACHE.name}")


def main():
    parser = argparse.ArgumentParser(description="Personal Brain DB 向量化工具")
    parser.add_argument("--rebuild",       action="store_true", help="重建整個索引")
    parser.add_argument("--query",         type=str, default="",  help="搜尋測試")
    parser.add_argument("--top",           type=int, default=5,   help="回傳前 N 筆")
    parser.add_argument("--type",          type=str, default="",  help="篩選類型：note/chat/bio")
    parser.add_argument("--contextualize", action="store_true",
                        help="The Illumination — 生成語境化段落摘要（Contextual Retrieval）")
    parser.add_argument("--hyqe",          action="store_true",
                        help="The Triple Echo — 生成假設問題（HyQE multi-view）")
    parser.add_argument("--ctx-model",     type=str, default=CTX_MODEL,
                        help=f"Contextual note / HyQE 使用的 Ollama 模型（預設 {CTX_MODEL}）")
    args = parser.parse_args()

    if args.query:
        results = search(args.query, args.top, args.type)
        if not results:
            print("The waters are still. No echoes found.")
            return
        print(f"\n搜尋：「{args.query}」\n{'='*55}")
        for i, r in enumerate(results, 1):
            actr_info = f"  ACT-R={r['actr_score']:+.3f}" if "actr_score" in r else ""
            print(f"\n#{i} 相關度 {r['score']:.3f}{actr_info} | {r['type']} | {r['date']}")
            print(f"   標題：{r['title']}")
            print(f"   路徑：{r['path']}")
            print(f"   摘要：{r['summary'][:100]}")
            print(f"   片段：{r['snippet'][:150]}")
        return

    if args.contextualize:
        contextualize_all(model=args.ctx_model, rebuild=args.rebuild)
        return

    if args.hyqe:
        hyqe_all(model=args.ctx_model, rebuild=args.rebuild)
        return

    build_index(rebuild=args.rebuild)


if __name__ == "__main__":
    main()
