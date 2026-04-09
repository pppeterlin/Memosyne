#!/usr/bin/env python3
"""
Personal Brain DB — MCP Server
讓 Claude Desktop / Cursor / 任何 MCP-compatible agent 可以搜尋個人記憶

安裝：pip install mcp
啟動：python3 mcp_server.py

Claude Desktop 設定（~/.claude/claude_desktop_config.json）：
{
  "mcpServers": {
    "personal-brain": {
      "command": "/Users/yourname/.virtualenvs/personal-memory/bin/python",
      "args": ["/Users/yourname/Documents/Python/personal-memory/Personal_Brain_DB/00_System/mcp_server.py"]
    }
  }
}
"""

from pathlib import Path
from mcp.server.fastmcp import FastMCP

BASE = Path(__file__).parent.parent
sys_dir = Path(__file__).parent

mcp = FastMCP("personal-brain")


def _get_collection():
    """懶載入 ChromaDB collection"""
    import chromadb
    from chromadb.utils import embedding_functions
    CHROMA_DIR  = sys_dir / "chroma_db"
    EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
    return client.get_or_create_collection(
        name="personal_brain",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


@mcp.tool()
def search_memory(query: str, top_k: int = 5) -> str:
    """
    語義搜尋個人記憶資料庫。
    可搜尋 Gemini 對話紀錄、個人手札、Profile 等所有內容。

    Args:
        query: 搜尋關鍵字或自然語言問題（支援中文）
        top_k: 回傳前幾筆結果（預設 5）
    """
    try:
        collection = _get_collection()
        if collection.count() == 0:
            return "⚠️ 向量索引尚未建立，請先執行：python3 vectorize.py"

        results = collection.query(
            query_texts=[query],
            n_results=min(top_k, collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        lines = [f"搜尋「{query}」，找到 {len(results['ids'][0])} 筆結果：\n"]
        for i, (doc, meta, dist) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )):
            score = round(1 - dist, 3)
            lines.append(
                f"{'─'*50}\n"
                f"#{i+1} 相關度 {score:.3f} | {meta.get('type','?')} | {meta.get('date','')}\n"
                f"標題：{meta.get('title','')}\n"
                f"路徑：{meta.get('path','')}\n"
                f"摘要：{meta.get('summary','')[:150]}\n"
                f"片段：{doc[:250]}\n"
            )
        return "\n".join(lines)

    except Exception as e:
        return f"搜尋失敗：{e}"


@mcp.tool()
def get_profile(section: str = "all") -> str:
    """
    讀取使用者個人 Profile。

    Args:
        section: 'all' | 'bio' | 'career' | 'family_pets' | 'preferences'
    """
    profile_dir = BASE / "10_Profile"
    files = {
        "bio":          profile_dir / "bio.md",
        "career":       profile_dir / "career.md",
        "family_pets":  profile_dir / "family_pets.md",
        "preferences":  profile_dir / "preferences.md",
    }

    if section == "all":
        parts = []
        for name, path in files.items():
            if path.exists():
                parts.append(f"## [{name}]\n{path.read_text(encoding='utf-8')}")
        return "\n\n".join(parts)

    path = files.get(section)
    if not path or not path.exists():
        return f"找不到 section：{section}，可用值：{list(files.keys())}"
    return path.read_text(encoding="utf-8")


@mcp.tool()
def list_journals(year: str = "", limit: int = 10) -> str:
    """
    列出個人手札清單。

    Args:
        year:  篩選年份，如 '2025'（空字串表示全部）
        limit: 最多顯示筆數（預設 10）
    """
    journal_dir = BASE / "30_Journal"
    pattern = f"{year}/**/*.md" if year else "**/*.md"
    files = sorted(journal_dir.glob(pattern), reverse=True)[:limit]

    if not files:
        return f"找不到手札（year={year}）"

    lines = [f"手札清單（最近 {len(files)} 筆）：\n"]
    for f in files:
        rel = str(f.relative_to(BASE))
        content = f.read_text(encoding="utf-8")
        # 取 summary
        summary = ""
        for line in content.split("\n"):
            if line.strip().startswith("summary:"):
                summary = line.split(":", 1)[1].strip().strip('"')[:80]
                break
        lines.append(f"• {rel}\n  {summary}")
    return "\n".join(lines)


@mcp.tool()
def read_file(path: str) -> str:
    """
    讀取 Personal_Brain_DB 內的任意檔案。

    Args:
        path: 相對於 Personal_Brain_DB 的路徑，如 '30_Journal/2026/260203.md'
    """
    target = BASE / path
    if not target.exists():
        return f"檔案不存在：{path}"
    if not str(target.resolve()).startswith(str(BASE.resolve())):
        return "禁止存取 Personal_Brain_DB 範圍外的檔案"
    return target.read_text(encoding="utf-8")


if __name__ == "__main__":
    mcp.run()
