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
from model_env import configure_hf_runtime

BASE = Path(__file__).parent.parent
sys_dir = Path(__file__).parent

configure_hf_runtime()

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
def search_memory(
    query: str,
    top_k: int = 5,
    return_parent: bool = False,
    muses: list[str] | None = None,
    auto_route: bool = False,
    muse_mode: str = "soft",
) -> str:
    """
    語義搜尋個人記憶資料庫（三路混合 + ACT-R 認知重排）。
    可搜尋 Gemini 對話紀錄、個人手札、Profile 等所有內容。

    搜尋架構：Dense 向量 + BM25 關鍵字 + Tapestry 圖譜 → RRF 融合 → ACT-R 認知衰減重排

    Args:
        query: 搜尋關鍵字或自然語言問題（支援中文）
        top_k: 回傳前幾筆結果（預設 5）
        return_parent: 若為 True，回傳命中 chunk 所屬的完整 parent section（Small-to-Big）
        muses: The Invocation — 指定繆思列表（例：["Clio","Calliope"]）
        auto_route: 自動路由 query 到最相關的 1–2 位繆思（忽略 muses 除非已指定）
        muse_mode: "soft"（命中加權 ×1.3）或 "hard"（只保留命中繆思的記憶）
    """
    try:
        from vectorize import search as hybrid_search
        results = hybrid_search(
            query, top_k=top_k, return_parent=return_parent,
            muses=muses, auto_route=auto_route, muse_mode=muse_mode,
        )

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
        action: 'all' | 'reflect' | 'hebbian' | 'forget' | 'naming' | 'ordeal' | 'stats'
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

        if action == "ordeal":
            from slumber import the_ordeal
            count = the_ordeal(dry_run=False)
            return f"The Ordeal: {count} non-NOOP operations recorded."

        if action == "all":
            lines = []
            from slumber import (reflect, hebbian_learning, strategic_forgetting,
                                 naming_rite, the_ordeal)
            path = reflect(dry_run=False)
            lines.append(f"Reflection: {path or 'skipped'}")
            heb = hebbian_learning(dry_run=False)
            lines.append(f"Hebbian: {heb} edges")
            fgt = strategic_forgetting(dry_run=False)
            lines.append(f"Lethe: {fgt} dormant")
            nrt = naming_rite(dry_run=False)
            lines.append(f"Naming Rite: {nrt} unified")
            ord_n = the_ordeal(dry_run=False)
            lines.append(f"Ordeal: {ord_n} verdicts")
            return "The Rite of Slumber complete.\n" + "\n".join(lines)

        return f"Unknown action: {action}. Use: all, reflect, hebbian, forget, naming, ordeal, stats"

    except Exception as e:
        return f"The Rite faltered: {e}"


@mcp.tool()
def get_entity_timeline(entity: str, limit: int = 30) -> str:
    """
    The Two Rivers — 回傳某實體（人/地/事件）在 Tapestry 中的時間線。
    每條邊顯示 t_valid_start → t_valid_end（NaT 表示目前仍有效）。

    Args:
        entity: 實體名稱（Person / Location / Event / Period）
        limit:  最多回傳筆數（預設 30）
    """
    try:
        from tapestry import get_entity_timeline as _timeline
        entries = _timeline(entity)
        if not entries:
            return f"「{entity}」— No timeline. Unknown or isolated entity."
        lines = [f"「{entity}」時間線（{len(entries)} 條邊）："]
        for e in entries[:limit]:
            tvs = e.get("tvs"); tve = e.get("tve")
            inv = e.get("inv") or "-"
            tve_str = str(tve) if tve else "still valid"
            lines.append(
                f"  {tvs} → {tve_str}  [{e['rel']}] {e['a']} → {e['b']}  inv_by={inv}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Timeline lookup faltered: {e}"


@mcp.tool()
def query_memory_at_time(query: str, timestamp: str, top_k: int = 5) -> str:
    """
    在指定時間點為錨點搜尋記憶：僅保留在 timestamp 當時其 Tapestry 邊仍有效的記憶。
    適用於回溯性問題（「在 2024 年時，我的同事是誰？」）。

    Args:
        query:     查詢問題
        timestamp: ISO 8601 格式（"2024-06-01" 或 "2024-06-01T00:00:00"）
        top_k:     回傳筆數
    """
    try:
        from datetime import datetime as _dt
        from vectorize import search as hybrid_search
        from tapestry import get_conn, edges_as_of, _REL_TABLES

        # 解析時間
        try:
            ts = _dt.fromisoformat(timestamp)
        except ValueError:
            return f"時間格式錯誤：{timestamp}（請用 ISO 8601，如 2024-06-01）"

        # 建立在 ts 當時有效的 memory 集合（以 mem_* 邊為依據）
        conn = get_conn()
        valid_mems: set[str] = set()
        for rel in ("mem_person", "mem_location", "mem_event", "mem_period"):
            for row in edges_as_of(conn, rel, ts):
                valid_mems.add(row["a"])

        raw = hybrid_search(query, top_k=top_k * 3)
        filtered = [r for r in raw if r.get("path") in valid_mems][:top_k]

        if not filtered:
            return f"搜尋「{query}」@ {ts.date()} — No memory valid at that time."
        lines = [f"搜尋「{query}」@ {ts.date()}（{len(filtered)} 筆，Tapestry 時空篩選）：\n"]
        for i, r in enumerate(filtered):
            lines.append(
                f"#{i+1} {r.get('score',0):.3f} | {r.get('type','?')} | {r.get('date','')}\n"
                f"  {r.get('title','')}  ←  {r.get('path','')}\n"
                f"  {r.get('summary','')[:120]}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Time-anchored search faltered: {e}"


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


# ──────────────────────────────────────────────────────────
#  Aletheia — 對話式記憶更正（Phase 6）
# ──────────────────────────────────────────────────────────

def _aletheia_summarize(entry: dict) -> str:
    op = entry.get("op", "?")
    path = entry.get("path", "?")
    log_id = entry.get("id", "(dry-run)")
    head = f"📜 Aletheia · {op} · {path} · log_id={log_id}"
    if op == "ADD_FACT":
        return f"{head}\n  added: {entry.get('added')}"
    if op == "INVALIDATE_FACT":
        return f"{head}\n  removed: {entry.get('removed')}"
    if op == "UPDATE_FACT":
        return (f"{head}\n  before: {entry.get('before')}\n"
                f"  after:  {entry.get('after')}")
    if op == "CORRECT_TEXT":
        return (f"{head}\n  old: {entry.get('old')}\n"
                f"  new: {entry.get('new')}")
    return head


@mcp.tool()
def aletheia_add_fact(path: str, fact: str, apply: bool = False) -> str:
    """
    Aletheia — 新增一條 personal_fact 到指定記憶。

    預設 dry-run（只顯示預期變更），apply=True 才寫入。所有操作寫入
    aletheia_log.jsonl，可用 aletheia_revert 復原。

    Args:
        path: vault 內相對路徑（如 30_Journal/2026/260223.md）
        fact: 要新增的事實句（建議 < 30 字）
        apply: True 才實際寫入
    """
    try:
        from aletheia import add_fact
        entry = add_fact(path, fact, apply=apply)
        return _aletheia_summarize(entry) + ("" if apply else "\n(dry-run; set apply=True to write)")
    except Exception as e:
        return f"Aletheia faltered: {e}"


@mcp.tool()
def aletheia_update_fact(path: str, old: str, new: str, apply: bool = False) -> str:
    """
    Aletheia — 更新一條 personal_fact（substring match，必須唯一）。

    Args:
        path: vault 內相對路徑
        old:  要被取代的事實（提供能唯一匹配的 substring 即可）
        new:  新的事實句
        apply: True 才實際寫入
    """
    try:
        from aletheia import update_fact
        entry = update_fact(path, old, new, apply=apply)
        return _aletheia_summarize(entry) + ("" if apply else "\n(dry-run)")
    except Exception as e:
        return f"Aletheia faltered: {e}"


@mcp.tool()
def aletheia_invalidate_fact(path: str, match: str, apply: bool = False) -> str:
    """
    Aletheia — 移除（invalidate）一條 personal_fact。

    Args:
        path:  vault 內相對路徑
        match: 要被移除的事實（substring，必須唯一）
        apply: True 才實際寫入
    """
    try:
        from aletheia import invalidate_fact
        entry = invalidate_fact(path, match, apply=apply)
        return _aletheia_summarize(entry) + ("" if apply else "\n(dry-run)")
    except Exception as e:
        return f"Aletheia faltered: {e}"


@mcp.tool()
def aletheia_correct_text(path: str, old: str, new: str, apply: bool = False) -> str:
    """
    Aletheia — 在記憶 body 做 literal substring 替換（old 必須唯一出現）。

    Args:
        path: vault 內相對路徑
        old:  要被替換的 substring（必須在 body 中唯一出現）
        new:  替換後的文字
        apply: True 才實際寫入
    """
    try:
        from aletheia import correct_text
        entry = correct_text(path, old, new, apply=apply)
        return _aletheia_summarize(entry) + ("" if apply else "\n(dry-run)")
    except Exception as e:
        return f"Aletheia faltered: {e}"


@mcp.tool()
def aletheia_revert(log_id: str, apply: bool = False) -> str:
    """
    Aletheia — 根據 log_id 復原先前的操作。

    Args:
        log_id: 先前操作回傳的 log_id
        apply:  True 才實際寫入
    """
    try:
        from aletheia import revert
        entry = revert(log_id, apply=apply)
        return _aletheia_summarize(entry) + ("" if apply else "\n(dry-run)")
    except Exception as e:
        return f"Aletheia faltered: {e}"


# ═══════════════════════════════════════════════════════════
#  The Invocation Protocol — meta-tool for routing (Phase 7)
# ═══════════════════════════════════════════════════════════

# 分類規則 — 關鍵字 → invocation class → 建議 tool + 範例
_INVOCATION_RULES: list[dict] = [
    # READ-TEMPORAL 要放前面，因為時間詞常伴隨 READ-RECALL 短語
    {
        "cls": "READ-TEMPORAL",
        "patterns": [
            "as of", "before the", "during the", "in 20",
            "那段時期", "那年", "那時", "時間軸", "歷年",
            # 年份 + 年
            r"\b(19|20)\d{2}",
        ],
        "tool": "query_memory_at_time  或  get_entity_timeline",
        "example": 'query_memory_at_time(query="Tokyo trip", timestamp="2023-06")',
        "notes": "時間點查詢用前者；實體完整軌跡用後者。timestamp 要 ISO 格式。",
    },
    {
        "cls": "WRITE-CORRECT",
        "patterns": [
            "記錯了", "應該是", "改成", "取消那個", "wrong", "correct me",
            "remove that fact", "that's not right", "打錯了",
        ],
        "tool": "aletheia_add_fact / update / invalidate / correct_text / revert",
        "example": 'aletheia_update_fact(path=..., old="2024", new="2025", apply=False)',
        "notes": "先 dry-run (apply=False) 看 diff，確認後才 apply=True。",
    },
    {
        "cls": "WRITE-INGEST",
        "patterns": ["記一下", "log this", "幫我存", "save this memory"],
        "tool": "（無 MCP 直接介面）",
        "example": "請使用者丟檔案到 spring/ 並跑 python3 00_System/ingest.py",
        "notes": "入庫需要 Oracle enrichment + chunking + 向量化，不是單一 call。",
    },
    {
        "cls": "READ-RECALL",
        "patterns": [
            "我上次", "我之前", "還記得", "什麼時候",
            "when did i", "what did i say", "remind me",
            "last time", "who did i", "我有沒有",
        ],
        "tool": "search_memory",
        "example": 'search_memory(query="東京 friend-A", top_k=5, auto_route=True)',
        "notes": "top_k 預設 5；結果稀疏時加 return_parent=True 取完整段落。",
    },
]


@mcp.tool()
def memosyne_guide(situation: str) -> str:
    """
    The Invocation Protocol — 當你（agent）不確定該用哪個 memory tool 時呼叫此工具。
    輸入自然語言描述的情境，回傳建議的 invocation class、對應 tool、範例呼叫與注意事項。

    這是 fallback / 教學工具，不應每輪都呼叫；正常流程請依循 memosyne-invocation skill
    的決策樹。

    Args:
        situation: 使用者剛剛說了什麼、或你想做什麼的自然語言描述
    """
    import re
    s = situation.lower()

    matched: list[dict] = []
    for rule in _INVOCATION_RULES:
        for pat in rule["patterns"]:
            # 簡單支援 regex 與 literal
            if pat.startswith(("\\b", "\\d", "(")) or any(ch in pat for ch in "^$[]"):
                if re.search(pat, s):
                    matched.append(rule); break
            elif pat.lower() in s:
                matched.append(rule); break

    if not matched:
        return (
            "無明確匹配 — 若使用者沒有問過去 / 沒提到個人實體 / 只是一般知識或閒聊，"
            "請**不要**呼叫 memory。若仍覺得應該查，預設 READ-CONTEXT："
            "`search_memory(query=<核心名詞>, top_k=3, auto_route=True)`，結果不顯眼使用。"
        )

    # 去重保序
    seen, out = set(), []
    for r in matched:
        if r["cls"] not in seen:
            out.append(r); seen.add(r["cls"])

    lines = [f"Invocation Protocol — {len(out)} 個可能類別：\n"]
    for r in out:
        lines.append(
            f"▸ **{r['cls']}**\n"
            f"   工具：{r['tool']}\n"
            f"   範例：{r['example']}\n"
            f"   備註：{r['notes']}\n"
        )
    lines.append(
        "\n通用守則：\n"
        "• 所有 Aletheia 操作預設 apply=False（dry-run）\n"
        "• 檢索結果請 interpret 而非 dump；top_k 越小越專注\n"
        "• 詳見 skill：Personal_Brain_DB/00_System/skills/memosyne-invocation/SKILL.md"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
