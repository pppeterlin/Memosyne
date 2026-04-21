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
def search_memory(query: str, top_k: int = 5, return_parent: bool = False) -> str:
    """
    語義搜尋個人記憶資料庫（三路混合 + ACT-R 認知重排）。
    可搜尋 Gemini 對話紀錄、個人手札、Profile 等所有內容。

    搜尋架構：Dense 向量 + BM25 關鍵字 + Tapestry 圖譜 → RRF 融合 → ACT-R 認知衰減重排

    Args:
        query: 搜尋關鍵字或自然語言問題（支援中文）
        top_k: 回傳前幾筆結果（預設 5）
        return_parent: 若為 True，回傳命中 chunk 所屬的完整 parent section（Small-to-Big）
    """
    try:
        from vectorize import search as hybrid_search
        results = hybrid_search(query, top_k=top_k, return_parent=return_parent)

        if not results:
            return f"搜尋「{query}」— The waters are still. No echoes found."

        # 記錄 MCP 存取來源
        try:
            from mneme_weight import record_access
            record_access([r["path"] for r in results], source="mcp_search")
        except ImportError:
            pass

        lines = [f"搜尋「{query}」，找到 {len(results)} 筆結果：\n"]
        for i, r in enumerate(results):
            actr_info = f"  ACT-R={r['actr_score']:+.3f}" if "actr_score" in r else ""
            # Small-to-Big: 若有 parent_section 則顯示完整段落
            content_field = r.get("parent_section", "") if return_parent else ""
            snippet_field = content_field or r.get("snippet", "")[:250]
            lines.append(
                f"{'─'*50}\n"
                f"#{i+1} 相關度 {r.get('score', 0):.3f}{actr_info} | "
                f"{r.get('type','?')} | {r.get('date','')}\n"
                f"標題：{r.get('title','')}\n"
                f"路徑：{r.get('path','')}\n"
                f"摘要：{r.get('summary','')[:150]}\n"
                f"時期：{r.get('period','')}\n"
                f"內容：{snippet_field}\n"
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

    # 記錄存取（The Chronicle of Mneme）
    try:
        from mneme_weight import record_access
        record_access([path], source="mcp_read")
    except ImportError:
        pass

    return target.read_text(encoding="utf-8")


@mcp.tool()
def optimize_memory(action: str = "all") -> str:
    """
    The Rite of Slumber — 記憶鞏固優化。
    定期執行以提煉洞察、強化關聯、清理冗餘、正規化實體。

    Args:
        action: 'all' | 'reflect' | 'hebbian' | 'forget' | 'naming' | 'stats'
    """
    try:
        if action == "stats":
            from slumber import slumber_stats
            import io, sys
            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            slumber_stats()
            sys.stdout = old_stdout
            return buf.getvalue()

        if action == "reflect":
            from slumber import reflect
            path = reflect(dry_run=False)
            return f"Reflection complete: {path}" if path else "Not enough recent memories for reflection."

        if action == "hebbian":
            from slumber import hebbian_learning
            count = hebbian_learning(dry_run=False)
            return f"Hebbian learning: {count} co_recalled edges strengthened."

        if action == "forget":
            from slumber import strategic_forgetting
            count = strategic_forgetting(dry_run=False)
            return f"Lethe Protocol: {count} memories marked dormant."

        if action == "naming":
            from slumber import naming_rite
            count = naming_rite(dry_run=False)
            return f"The Naming Rite: {count} person names unified."

        if action == "all":
            lines = []
            from slumber import reflect, hebbian_learning, strategic_forgetting, naming_rite
            path = reflect(dry_run=False)
            lines.append(f"Reflection: {path or 'skipped'}")
            heb = hebbian_learning(dry_run=False)
            lines.append(f"Hebbian: {heb} edges")
            fgt = strategic_forgetting(dry_run=False)
            lines.append(f"Lethe: {fgt} dormant")
            nrt = naming_rite(dry_run=False)
            lines.append(f"Naming Rite: {nrt} unified")
            return "The Rite of Slumber complete.\n" + "\n".join(lines)

        return f"Unknown action: {action}. Use: all, reflect, hebbian, forget, naming, stats"

    except Exception as e:
        return f"The Rite faltered: {e}"


@mcp.tool()
def get_memory_health() -> str:
    """
    The Chronicle of Mneme — 記憶存取健康報告。
    顯示存取紀錄統計、ACT-R 最活躍記憶、以及搜尋來源分佈。
    """
    try:
        from mneme_weight import chronicle_stats, compute_activation
        stats = chronicle_stats()
        lines = [
            "📜 The Chronicle of Mneme — Memory Health Report\n",
            f"總存取次數：{stats['total_events']}",
            f"已觸碰記憶：{stats['unique_memories']}",
            f"存取來源：{stats['sources']}",
        ]
        if stats.get("top_accessed"):
            lines.append("\n最活躍記憶（ACT-R 激活分數）：")
            for path, cnt in stats["top_accessed"][:5]:
                score = compute_activation(path)
                lines.append(f"  [{cnt:3d} 次] ACT-R={score:+.3f}  {path}")
        return "\n".join(lines)
    except ImportError:
        return "The Chronicle module is not available."
    except Exception as e:
        return f"The Chronicle faltered: {e}"


if __name__ == "__main__":
    mcp.run()
