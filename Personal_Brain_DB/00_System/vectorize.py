#!/usr/bin/env python3
"""
Personal Brain DB — 向量化索引

設計原則（第一原理）
─────────────────────
現代 dense embedding 模型（paraphrase-multilingual-MiniLM-L12-v2）已能捕捉語義，
不需要另外做關鍵詞提取。真正影響精準度的是：

1. Metadata 注入到 chunk 文字
   不是把 type/date/title 存進 metadata filter 欄位，而是 prepend 到 chunk 本身：
   "[手札][2026-02-03][寵物] 今天是小貓回家的第一天..."
   → embedding 本身就帶有「這是什麼類型、什麼時間、什麼主題」的語義

2. 語義切段（段落為單位，不是字數）
   用 \\n\\n（空行）為邊界切段，保留完整想法。
   字數硬切會把一個完整的句子/段落切斷，扭曲語義向量方向。

3. 雙粒度索引
   每個文件 = 1個「文件摘要 chunk」+ N個「段落 chunk」
   - 摘要 chunk：捕捉整體主題（適合「這件事我有沒有記錄過？」）
   - 段落 chunk：捕捉細節（適合「那件事的具體內容是什麼？」）
   - 兩種都用同一個 collection，靠 chunk_type metadata 區分

執行方式：
  python3 vectorize.py              # 增量更新
  python3 vectorize.py --rebuild    # 重建整個向量資料庫
  python3 vectorize.py --query "深圳工作" --top 5
"""

import argparse
import re
from pathlib import Path

BASE        = Path(__file__).parent.parent
CHROMA_DIR  = Path(__file__).parent / "chroma_db"
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
COLLECTION  = "personal_brain"
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
    """回傳 (metadata_dict, body_text)"""
    fm = {}
    if not content.startswith("---"):
        return fm, content
    end = content.find("---", 3)
    if end < 0:
        return fm, content
    for line in content[3:end].split("\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    body = content[end + 3:].strip()
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

def make_prefix(fm: dict) -> str:
    """
    建立 metadata 前綴，注入到每個 chunk 的文字開頭。
    格式：[type][date][tag1,tag2] title:
    """
    doc_type = fm.get("type", "note")
    date     = fm.get("date_created", "")[:10]
    tags_raw = fm.get("tags", "")
    # 清理 tags（可能是 JSON array 字串或逗號分隔）
    tags = re.findall(r'[\w\u4e00-\u9fff]+', tags_raw)
    tags_str = ",".join(tags[:4]) if tags else ""
    title    = fm.get("title", "")

    prefix = f"[{doc_type}][{date}]"
    if tags_str:
        prefix += f"[{tags_str}]"
    if title:
        prefix += f" {title}:"
    return prefix


def build_chunks(rel_path: str, fm: dict, body: str) -> list[dict]:
    """
    為一個文件建立所有 chunks。
    回傳 list of {id, text, meta}
    """
    prefix = make_prefix(fm)
    meta_base = {
        "path":       rel_path,
        "title":      fm.get("title", Path(rel_path).stem),
        "type":       fm.get("type", "note"),
        "date":       fm.get("date_created", "")[:10],
        "source":     fm.get("source", ""),
        "summary":    fm.get("summary", "")[:300],
    }
    chunks = []

    # ① 文件摘要 chunk（document-level）
    summary_text = fm.get("summary", "").strip()
    title_text   = fm.get("title", "").strip()
    tags_text    = fm.get("tags", "").strip()
    doc_summary  = f"{prefix}\n{title_text}\n{tags_text}\n{summary_text}".strip()
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


def _print_stats(col):
    """印出各 type 的文件數統計"""
    all_meta = col.get(include=["metadatas"])["metadatas"]
    from collections import Counter
    by_type  = Counter(m.get("type", "?")  for m in all_meta if m.get("chunk_type") == "summary")
    by_chunk = Counter(m.get("chunk_type") for m in all_meta)
    print(f"\n  chunk 類型：{dict(by_chunk)}")
    print(f"  文件類型：{dict(by_type)}")


# ─── 搜尋 ────────────────────────────────────────────────────

def search(query: str, top_k: int = 5, doc_type: str = "") -> list[dict]:
    _, col = get_collection()
    if col.count() == 0:
        print("⚠️  向量索引尚未建立，請先執行：python3 vectorize.py")
        return []

    # ChromaDB 多條件需用 $and
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

    output = []
    seen_paths = set()   # 去除同一文件的重複結果
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
            "score":   round(1 - dist, 4),
            "title":   meta.get("title", ""),
            "path":    path,
            "date":    meta.get("date", ""),
            "type":    meta.get("type", ""),
            "summary": meta.get("summary", ""),
            "snippet": doc[doc.find("\n") + 1:][:200] if "\n" in doc else doc[:200],
        })
    return output


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
