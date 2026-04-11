#!/usr/bin/env python3
"""
Personal Brain DB — 向量化索引 + BM25 Hybrid Search

索引架構：
─────────────────────────────────────────────
1. Dense Vector（ChromaDB）
   paraphrase-multilingual-MiniLM-L12-v2 捕捉語義相似性
   enrichment metadata prefix 注入到每個 chunk

2. BM25 關鍵字索引（rank-bm25）
   對「精確詞彙」查詢有優勢（地名、人名、專有名詞）
   語料包含 enrichment 欄位（locations / period），
   讓「雲南」查詢能找到 locations 含「雲南/CityA」的記憶

3. Hybrid 融合（RRF：Reciprocal Rank Fusion）
   Dense top-15 + BM25 top-15 → RRF 合併 → FlashRank 精排 top-5
   互補覆蓋語義模糊 vs 精確詞彙兩種查詢類型

執行方式：
  python3 vectorize.py              # 增量更新（vector + bm25）
  python3 vectorize.py --rebuild    # 重建所有索引
  python3 vectorize.py --query "Osaka工作" --top 5
"""

import argparse
import pickle
import re
from pathlib import Path

BASE         = Path(__file__).parent.parent
CHROMA_DIR   = Path(__file__).parent / "chroma_db"
BM25_PATH    = Path(__file__).parent / "bm25_index.pkl"
EMBED_MODEL  = "paraphrase-multilingual-MiniLM-L12-v2"
COLLECTION   = "personal_brain"
MIN_PARA_LEN = 25   # 少於此字數的段落略過（通常是標題殘留）


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
            "meta": {**meta_base, "chunk_type": "summary", "chunk_index": 0},
        })

    # ② 段落 chunks（paragraph-level）
    paras = semantic_paragraphs(body)
    for i, para in enumerate(paras):
        injected = f"{prefix}\n{para}"
        chunks.append({
            "id":   f"{rel_path}::para{i}",
            "text": injected,
            "meta": {**meta_base, "chunk_type": "paragraph", "chunk_index": i + 1},
        })

    return chunks


# ─── 主流程 ─────────────────────────────────────────────────

EXCLUDE_FILES = {"README.md", ".cursorrules"}

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
        rel = str(md_file.relative_to(BASE))
        fm, body = parse_frontmatter(content)
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

    # 只索引 paragraph chunks（summary chunk 會重複，不加入）
    para_chunks = [c for c in all_chunks if c["meta"].get("chunk_type") == "paragraph"]

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
            "snippet":   text[text.find("\n") + 1:][:200] if "\n" in text else text[:200],
        })
        if len(output) >= top_k:
            break

    return output


def _rrf_merge_multi(
    ranked_lists: list[list[dict]],
    k: int = 60,
) -> list[dict]:
    """
    多路 Reciprocal Rank Fusion。

    RRF(d) = Σ 1/(k + rank_i(d))
    k=60 是常用預設值（論文建議）。

    接受任意數量的排名列表（dense / bm25 / graph），
    回傳按 RRF 分數排序的去重結果列表。
    """
    rrf_scores: dict[str, float] = {}
    all_items:  dict[str, dict]  = {}

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            path = item["path"]
            rrf_scores[path] = rrf_scores.get(path, 0.0) + 1.0 / (k + rank + 1)
            if path not in all_items:
                all_items[path] = item

    ranked_final = sorted(rrf_scores.items(), key=lambda x: -x[1])
    return [all_items[path] for path, _ in ranked_final]


def _rrf_merge(
    dense_results: list[dict],
    bm25_results:  list[dict],
    k: int = 60,
) -> list[dict]:
    """兩路 RRF（保留向後兼容）。"""
    return _rrf_merge_multi([dense_results, bm25_results], k=k)


# ─── 搜尋 ────────────────────────────────────────────────────

def search_dense(query: str, top_k: int = 15, doc_type: str = "") -> list[dict]:
    """Pure dense vector search（ChromaDB cosine similarity）。"""
    _, col = get_collection()
    if col.count() == 0:
        return []

    if doc_type:
        where = {"$and": [{"chunk_type": {"$eq": "paragraph"}}, {"type": {"$eq": doc_type}}]}
    else:
        where = {"chunk_type": {"$eq": "paragraph"}}

    results = col.query(
        query_texts  = [query],
        n_results    = min(top_k, col.count()),
        where        = where,
        include      = ["documents", "metadatas", "distances"],
    )

    output     = []
    seen_paths = set()
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        path = meta.get("path", "")
        if path in seen_paths:
            continue
        seen_paths.add(path)
        output.append({
            "score":     round(1 - dist, 4),
            "title":     meta.get("title", ""),
            "path":      path,
            "date":      meta.get("date", ""),
            "type":      meta.get("type", ""),
            "summary":   meta.get("summary", ""),
            "period":    meta.get("period", ""),
            "locations": meta.get("locations", ""),
            "snippet":   doc[doc.find("\n") + 1:][:200] if "\n" in doc else doc[:200],
        })
    return output


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
            "snippet":   "",   # graph search 不提供 snippet
        })
        if len(output) >= top_k:
            break

    return output


def search(query: str, top_k: int = 5, doc_type: str = "") -> list[dict]:
    """
    三路 Hybrid search：Dense（ChromaDB）+ BM25 + Tapestry Graph → RRF 融合 → top_k 結果。

    降級策略：
    - 無 BM25 索引 → 跳過 BM25
    - 無 Tapestry  → 跳過圖搜尋
    - 三路都有    → RRF 三路合併
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
        return dense_results[:top_k]

    merged = _rrf_merge_multi(ranked_lists)
    return merged[:top_k]


# ─── CLI ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Personal Brain DB 向量化工具")
    parser.add_argument("--rebuild",   action="store_true", help="重建整個索引")
    parser.add_argument("--query",     type=str, default="",  help="搜尋測試")
    parser.add_argument("--top",       type=int, default=5,   help="回傳前 N 筆")
    parser.add_argument("--type",      type=str, default="",  help="篩選類型：note/chat/bio")
    args = parser.parse_args()

    if args.query:
        results = search(args.query, args.top, args.type)
        if not results:
            return
        print(f"\n搜尋：「{args.query}」\n{'='*55}")
        for i, r in enumerate(results, 1):
            print(f"\n#{i} 相關度 {r['score']:.3f} | {r['type']} | {r['date']}")
            print(f"   標題：{r['title']}")
            print(f"   路徑：{r['path']}")
            print(f"   摘要：{r['summary'][:100]}")
            print(f"   片段：{r['snippet'][:150]}")
        return

    build_index(rebuild=args.rebuild)


if __name__ == "__main__":
    main()
