#!/usr/bin/env python3
"""
Memosyne — The Tapestry (tapestry.py)

圖拓樸記憶關聯層。記憶庫越大，圖的價值越高。

節點類型（Node types）：
  memory   — 記憶檔案本身（memory path 為 id）
  person   — 人物（丈母娘、老婆）
  location — 地點（鄭州、深圳、大理）
  event    — 事件（丈母娘專案、CartaBio入職）
  period   — 時期（2025深圳求職期）

邊類型（Edge types）：
  memory → entity  : mentions       （記憶提及此實體）
  person → location: located_in     （人物與地點的關聯）
  event  → location: happened_at    （事件發生地）
  person → event   : involved_in    （人物參與事件）
  memory → period  : during         （記憶所屬時期）

用法：
  from tapestry import load_tapestry, weave_memory, graph_search, backfill_from_vault
"""

import json
from pathlib import Path

import networkx as nx

TAPESTRY_PATH = Path(__file__).parent / "tapestry.json"
BASE          = Path(__file__).parent.parent

ENTITY_NODE_TYPES = {"person", "location", "event", "period"}


# ─── 載入 / 儲存 ─────────────────────────────────────────────

def load_tapestry() -> nx.DiGraph:
    """從磁碟載入 Tapestry。若不存在則回傳空圖。"""
    if not TAPESTRY_PATH.exists():
        return nx.DiGraph()
    data = json.loads(TAPESTRY_PATH.read_text(encoding="utf-8"))
    return nx.node_link_graph(data, directed=True, multigraph=False)


def save_tapestry(G: nx.DiGraph) -> None:
    """將 Tapestry 序列化到磁碟。"""
    data = nx.node_link_data(G)
    TAPESTRY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─── 節點 / 邊 操作 ──────────────────────────────────────────

def _upsert_entity(G: nx.DiGraph, name: str, node_type: str) -> None:
    """若節點不存在則新增；若已存在則保留現有屬性。"""
    if not G.has_node(name):
        G.add_node(name, type=node_type, aliases=[])


def _add_edge_once(G: nx.DiGraph, src: str, dst: str, rel: str, **attrs) -> None:
    """只在邊不存在時才新增（避免覆蓋現有 attrs）。"""
    if not G.has_edge(src, dst):
        G.add_edge(src, dst, rel=rel, **attrs)


# ─── 主要織入函式 ─────────────────────────────────────────────

def weave_memory(
    G: nx.DiGraph,
    memory_path: str,
    enrichment: dict,
) -> nx.DiGraph:
    """
    The Weaving — 將一份記憶的 enrichment 結果織入 Tapestry。

    呼叫時機：enrich.py 每次成功增強後立即呼叫。
    memory_path: 相對於 Personal_Brain_DB 的路徑（如 "30_Journal/2025/250801.md"）
    """
    entities       = enrichment.get("entities", {})
    period         = enrichment.get("period", "")
    locations      = entities.get("locations", [])
    people         = entities.get("people", [])
    events         = entities.get("events", [])
    personal_facts = enrichment.get("personal_facts", [])

    # ── 記憶節點 ──────────────────────────────────────────────
    if not G.has_node(memory_path):
        G.add_node(memory_path, type="memory")

    # ── 時期節點 ──────────────────────────────────────────────
    if period:
        _upsert_entity(G, period, "period")
        _add_edge_once(G, memory_path, period, rel="during")

    # ── 地點節點 + 邊 ─────────────────────────────────────────
    for loc in locations:
        _upsert_entity(G, loc, "location")
        _add_edge_once(G, memory_path, loc, rel="mentions")

    # ── 人物節點 + 邊 ─────────────────────────────────────────
    for person in people:
        _upsert_entity(G, person, "person")
        _add_edge_once(G, memory_path, person, rel="mentions")

    # ── 事件節點 + 邊 ─────────────────────────────────────────
    for event in events:
        _upsert_entity(G, event, "event")
        _add_edge_once(G, memory_path, event, rel="mentions")
        # 事件 → 地點
        for loc in locations:
            _add_edge_once(G, event, loc, rel="happened_at")
        # 人物 → 事件
        for person in people:
            _add_edge_once(G, person, event, rel="involved_in")

    # ── personal_facts 解析：人物 → 地點 關聯 ─────────────────
    # 若某個 personal_fact 同時包含人物名稱和地點名稱，建立 located_in 邊
    for fact in personal_facts:
        for person in people:
            for loc in locations:
                if person in fact and loc in fact:
                    _add_edge_once(G, person, loc, rel="located_in", evidence=fact[:80])

    return G


# ─── 圖搜尋 ──────────────────────────────────────────────────

def graph_search(
    query_terms: list[str],
    G: nx.DiGraph | None = None,
    hops: int = 2,
) -> list[str]:
    """
    從 query_terms 出發，在圖上遍歷，回傳相關的 memory path 列表（按關聯強度排序）。

    流程：
      1. 找出與 query_terms 匹配的實體節點（精確 + 子字串）
      2. 從這些實體節點做 BFS（hops 步）
      3. 蒐集所有可達的 memory 節點，按命中次數排序

    hops=2 表示可以跨越「人物 → 事件 → 記憶」這樣的兩跳路徑。
    """
    if G is None:
        G = load_tapestry()
    if not G.nodes:
        return []

    # ── Step 1：找匹配的實體節點 ──────────────────────────────
    matched_entities: set[str] = set()
    for term in query_terms:
        if not term or len(term) < 2:
            continue
        for node, attrs in G.nodes(data=True):
            if attrs.get("type") in ENTITY_NODE_TYPES and term in node:
                matched_entities.add(node)

    if not matched_entities:
        return []

    # ── Step 2：BFS 遍歷，蒐集 memory 節點 ───────────────────
    memory_scores: dict[str, float] = {}

    for entity in matched_entities:
        visited  = {entity}
        frontier = {entity}

        for hop in range(hops):
            next_frontier: set[str] = set()
            weight = 1.0 / (hop + 1)   # 距離越遠，權重越低

            for node in frontier:
                neighbors = set(G.predecessors(node)) | set(G.successors(node))
                for nbr in neighbors:
                    if nbr in visited:
                        continue
                    visited.add(nbr)
                    nbr_type = G.nodes[nbr].get("type")

                    if nbr_type == "memory":
                        # hop=0 的直接鄰居給更高權重
                        bonus = 2.0 if hop == 0 else 1.0
                        memory_scores[nbr] = memory_scores.get(nbr, 0.0) + weight * bonus
                    else:
                        next_frontier.add(nbr)

            frontier = next_frontier

    ranked = sorted(memory_scores.items(), key=lambda x: -x[1])
    return [path for path, _ in ranked]


# ─── Backfill：從現有記憶庫重建 Tapestry ──────────────────────

def backfill_from_vault(verbose: bool = True) -> tuple[int, int]:
    """
    掃描 Personal_Brain_DB 所有已增強（有 enriched_at）的 .md 記憶，
    重建完整 Tapestry。

    回傳 (記憶數, 節點數)。
    """
    import re
    import yaml

    EXCLUDE = {"00_System"}
    G = nx.DiGraph()
    mem_count = 0

    for md_file in sorted(BASE.rglob("*.md")):
        if any(part in EXCLUDE for part in md_file.parts):
            continue
        if md_file.name in {"README.md", ".cursorrules"}:
            continue

        content = md_file.read_text(encoding="utf-8")
        if "enriched_at:" not in content:
            continue

        # 解析 frontmatter
        if not content.startswith("---"):
            continue
        end = content.find("\n---", 3)
        if end < 0:
            continue
        raw_fm = content[3:end]
        clean_lines = [ln for ln in raw_fm.split("\n") if not ln.strip().startswith("#")]
        try:
            fm = yaml.safe_load("\n".join(clean_lines)) or {}
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue

        entities = fm.get("entities") or {}
        if not isinstance(entities, dict):
            entities = {}

        enrichment = {
            "entities": {
                "locations": entities.get("locations") or [],
                "people":    entities.get("people")    or [],
                "events":    entities.get("events")    or [],
            },
            "period":         fm.get("period", "") or "",
            "personal_facts": fm.get("personal_facts") or [],
        }

        rel_path = str(md_file.relative_to(BASE))
        weave_memory(G, rel_path, enrichment)
        mem_count += 1

    save_tapestry(G)
    node_count = G.number_of_nodes()

    if verbose:
        entity_nodes = [n for n, d in G.nodes(data=True) if d.get("type") in ENTITY_NODE_TYPES]
        mem_nodes    = [n for n, d in G.nodes(data=True) if d.get("type") == "memory"]
        print(f"[TAPESTRY] 織入 {mem_count} 份記憶 → {len(mem_nodes)} memory nodes, "
              f"{len(entity_nodes)} entity nodes, {G.number_of_edges()} edges")
        print(f"[TAPESTRY] Tapestry 已儲存：{TAPESTRY_PATH.name}")

    return mem_count, node_count


# ─── 統計 ────────────────────────────────────────────────────

def tapestry_stats(G: nx.DiGraph | None = None) -> dict:
    """回傳 Tapestry 基本統計資訊。"""
    if G is None:
        G = load_tapestry()

    from collections import Counter
    type_counts = Counter(d.get("type", "?") for _, d in G.nodes(data=True))
    rel_counts  = Counter(d.get("rel", "?") for _, _, d in G.edges(data=True))

    return {
        "nodes":    G.number_of_nodes(),
        "edges":    G.number_of_edges(),
        "by_type":  dict(type_counts),
        "by_rel":   dict(rel_counts),
    }


# ─── CLI ────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Memosyne Tapestry — 記憶圖拓樸")
    ap.add_argument("--backfill", action="store_true", help="從現有記憶庫重建 Tapestry")
    ap.add_argument("--stats",    action="store_true", help="顯示 Tapestry 統計")
    ap.add_argument("--search",   type=str, default="", help="圖搜尋測試（逗號分隔關鍵詞）")
    args = ap.parse_args()

    if args.backfill:
        print("[TAPESTRY] The Grand Weaving begins — rebuilding from the Vault...")
        backfill_from_vault(verbose=True)

    elif args.stats:
        G = load_tapestry()
        stats = tapestry_stats(G)
        print(f"[TAPESTRY] 節點：{stats['nodes']}  邊：{stats['edges']}")
        print(f"  節點類型：{stats['by_type']}")
        print(f"  邊類型：{stats['by_rel']}")

    elif args.search:
        terms = [t.strip() for t in args.search.split(",") if t.strip()]
        G = load_tapestry()
        results = graph_search(terms, G)
        print(f"[TAPESTRY] 搜尋：{terms}")
        if not results:
            print("  The waters are still. No echoes found.")
        else:
            for i, path in enumerate(results[:10], 1):
                print(f"  #{i} {path}")

    else:
        ap.print_help()
