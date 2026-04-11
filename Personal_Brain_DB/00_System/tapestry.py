#!/usr/bin/env python3
"""
Memosyne — The Tapestry (tapestry.py)

圖拓樸記憶關聯層，以 kuzu 嵌入式圖資料庫儲存。

記憶庫越大，圖的查詢效能優勢越明顯：
  networkx + JSON  → 每次查詢需把整個圖載入記憶體，純 Python BFS
  kuzu             → 磁碟常駐、有索引、Cypher 查詢、原生多跳遍歷

節點類型（Node tables）：
  Memory   — 記憶檔案（path 為 primary key）
  Person   — 人物（name 為 primary key）
  Location — 地點
  Event    — 事件
  Period   — 時期

關聯類型（Rel tables）：
  mem_person   : Memory → Person    （記憶提及人物）
  mem_location : Memory → Location  （記憶提及地點）
  mem_event    : Memory → Event     （記憶提及事件）
  mem_period   : Memory → Period    （記憶所屬時期）
  event_loc    : Event  → Location  （事件發生地）
  person_loc   : Person → Location  （人物與地點關聯，含 evidence）
  person_event : Person → Event     （人物參與事件）

用法：
  python3 tapestry.py --backfill        # 從現有記憶庫重建圖
  python3 tapestry.py --stats           # 顯示統計
  python3 tapestry.py --search "鄭州"   # 圖搜尋測試
"""

import re
import sys
from pathlib import Path

import kuzu

BASE          = Path(__file__).parent.parent
TAPESTRY_DB   = Path(__file__).parent / "tapestry_db"

# ─── 資料庫初始化 ────────────────────────────────────────────

def _open() -> tuple[kuzu.Database, kuzu.Connection]:
    """開啟（或建立）kuzu DB，回傳 (db, conn)。"""
    db   = kuzu.Database(str(TAPESTRY_DB))
    conn = kuzu.Connection(db)
    return db, conn


def _init_schema(conn: kuzu.Connection) -> None:
    """建立 schema（IF NOT EXISTS，冪等）。"""
    stmts = [
        # Node tables
        "CREATE NODE TABLE IF NOT EXISTS Memory(path STRING, PRIMARY KEY(path))",
        "CREATE NODE TABLE IF NOT EXISTS Person(name STRING, PRIMARY KEY(name))",
        "CREATE NODE TABLE IF NOT EXISTS Location(name STRING, PRIMARY KEY(name))",
        "CREATE NODE TABLE IF NOT EXISTS Event(name STRING, PRIMARY KEY(name))",
        "CREATE NODE TABLE IF NOT EXISTS Period(name STRING, PRIMARY KEY(name))",
        # Rel tables
        "CREATE REL TABLE IF NOT EXISTS mem_person(FROM Memory TO Person)",
        "CREATE REL TABLE IF NOT EXISTS mem_location(FROM Memory TO Location)",
        "CREATE REL TABLE IF NOT EXISTS mem_event(FROM Memory TO Event)",
        "CREATE REL TABLE IF NOT EXISTS mem_period(FROM Memory TO Period)",
        "CREATE REL TABLE IF NOT EXISTS event_loc(FROM Event TO Location)",
        "CREATE REL TABLE IF NOT EXISTS person_loc(FROM Person TO Location, evidence STRING)",
        "CREATE REL TABLE IF NOT EXISTS person_event(FROM Person TO Event)",
    ]
    for s in stmts:
        conn.execute(s)


def get_conn() -> kuzu.Connection:
    """取得已初始化 schema 的 Connection（每次呼叫皆開新 conn）。"""
    _, conn = _open()
    _init_schema(conn)
    return conn


# ─── MERGE helpers ───────────────────────────────────────────

def _merge_node(conn: kuzu.Connection, label: str, name: str) -> None:
    conn.execute(f"MERGE (n:{label} {{name: $n}})", {"n": name})


def _merge_memory(conn: kuzu.Connection, path: str) -> None:
    conn.execute("MERGE (m:Memory {path: $p})", {"p": path})


def _merge_rel(conn: kuzu.Connection, cypher: str, params: dict) -> None:
    """用 MERGE 建立關係（節點必須已存在）。"""
    conn.execute(cypher, params)


# ─── 主要織入函式 ─────────────────────────────────────────────

def weave_memory(
    conn: kuzu.Connection,
    memory_path: str,
    enrichment: dict,
) -> None:
    """
    The Weaving — 將一份記憶的 enrichment 結果織入 Tapestry。

    呼叫時機：enrich.py 每次成功增強後立即呼叫。
    conn 傳入已開啟的 Connection（由呼叫方管理生命週期）。
    """
    entities       = enrichment.get("entities", {})
    period         = enrichment.get("period", "") or ""
    locations      = [l for l in entities.get("locations", []) if l]
    people         = [p for p in entities.get("people",    []) if p]
    events         = [e for e in entities.get("events",    []) if e]
    personal_facts = enrichment.get("personal_facts", []) or []

    # ── Memory node ───────────────────────────────────────────
    _merge_memory(conn, memory_path)

    # ── Period ────────────────────────────────────────────────
    if period:
        _merge_node(conn, "Period", period)
        conn.execute("""
            MATCH (m:Memory {path: $mp}), (p:Period {name: $pn})
            MERGE (m)-[:mem_period]->(p)
        """, {"mp": memory_path, "pn": period})

    # ── Locations ─────────────────────────────────────────────
    for loc in locations:
        _merge_node(conn, "Location", loc)
        conn.execute("""
            MATCH (m:Memory {path: $mp}), (l:Location {name: $ln})
            MERGE (m)-[:mem_location]->(l)
        """, {"mp": memory_path, "ln": loc})

    # ── People ────────────────────────────────────────────────
    for person in people:
        _merge_node(conn, "Person", person)
        conn.execute("""
            MATCH (m:Memory {path: $mp}), (p:Person {name: $pn})
            MERGE (m)-[:mem_person]->(p)
        """, {"mp": memory_path, "pn": person})

    # ── Events ────────────────────────────────────────────────
    for event in events:
        _merge_node(conn, "Event", event)
        conn.execute("""
            MATCH (m:Memory {path: $mp}), (e:Event {name: $en})
            MERGE (m)-[:mem_event]->(e)
        """, {"mp": memory_path, "en": event})
        # Event → Location
        for loc in locations:
            conn.execute("""
                MATCH (e:Event {name: $en}), (l:Location {name: $ln})
                MERGE (e)-[:event_loc]->(l)
            """, {"en": event, "ln": loc})
        # Person → Event
        for person in people:
            conn.execute("""
                MATCH (p:Person {name: $pn}), (e:Event {name: $en})
                MERGE (p)-[:person_event]->(e)
            """, {"pn": person, "en": event})

    # ── personal_facts → Person located_in Location ───────────
    for fact in personal_facts:
        if not isinstance(fact, str):
            continue
        for person in people:
            for loc in locations:
                if person in fact and loc in fact:
                    conn.execute("""
                        MATCH (p:Person {name: $pn}), (l:Location {name: $ln})
                        MERGE (p)-[:person_loc {evidence: $ev}]->(l)
                    """, {"pn": person, "ln": loc, "ev": fact[:80]})


# ─── 圖搜尋 ──────────────────────────────────────────────────

def graph_search(
    query_terms: list[str],
    conn: kuzu.Connection | None = None,
    hops: int = 2,
) -> list[str]:
    """
    從 query_terms 出發，以 Cypher 遍歷圖，回傳相關 memory path 列表。

    hops=2 支援：Memory → Entity → Entity → Memory 的兩跳路徑。
    回傳按關聯強度（命中次數）降序排列的 path list。
    """
    _own_conn = conn is None
    if _own_conn:
        conn = get_conn()

    memory_scores: dict[str, float] = {}

    for term in query_terms:
        if not term or len(term) < 2:
            continue
        pat = f"%{term}%"

        # ── 1-hop：Memory 直接 mentions 相符實體 ─────────────
        queries_1hop = [
            # Memory → Location
            ("MATCH (m:Memory)-[:mem_location]->(l:Location) "
             "WHERE l.name CONTAINS $t RETURN DISTINCT m.path AS path", 2.0),
            # Memory → Person
            ("MATCH (m:Memory)-[:mem_person]->(p:Person) "
             "WHERE p.name CONTAINS $t RETURN DISTINCT m.path AS path", 2.0),
            # Memory → Event
            ("MATCH (m:Memory)-[:mem_event]->(e:Event) "
             "WHERE e.name CONTAINS $t RETURN DISTINCT m.path AS path", 2.0),
            # Memory → Period
            ("MATCH (m:Memory)-[:mem_period]->(pr:Period) "
             "WHERE pr.name CONTAINS $t RETURN DISTINCT m.path AS path", 1.5),
        ]

        if hops >= 2:
            # ── 2-hop：透過實體間關係找到更多記憶 ─────────────
            queries_2hop = [
                # Memory → Person → Location（Person 住在目標地點）
                ("MATCH (m:Memory)-[:mem_person]->(p:Person)-[:person_loc]->(l:Location) "
                 "WHERE l.name CONTAINS $t RETURN DISTINCT m.path AS path", 1.0),
                # Memory → Event → Location（事件發生在目標地點）
                ("MATCH (m:Memory)-[:mem_event]->(e:Event)-[:event_loc]->(l:Location) "
                 "WHERE l.name CONTAINS $t RETURN DISTINCT m.path AS path", 1.0),
                # Memory → Person → Event（人物參與目標事件）
                ("MATCH (m:Memory)-[:mem_person]->(p:Person)-[:person_event]->(e:Event) "
                 "WHERE e.name CONTAINS $t RETURN DISTINCT m.path AS path", 1.0),
                # Memory → Location ← Event ← Person（反向：地點關聯人物）
                ("MATCH (m:Memory)-[:mem_location]->(l:Location)<-[:event_loc]-(e:Event) "
                 "WHERE e.name CONTAINS $t RETURN DISTINCT m.path AS path", 0.8),
            ]
        else:
            queries_2hop = []

        for cypher, weight in queries_1hop + queries_2hop:
            try:
                result = conn.execute(cypher, {"t": term})
                df = result.get_as_df()
                for path in df["path"].tolist():
                    memory_scores[path] = memory_scores.get(path, 0.0) + weight
            except Exception:
                continue

    ranked = sorted(memory_scores.items(), key=lambda x: -x[1])
    return [path for path, _ in ranked]


# ─── Backfill：從現有記憶庫重建 Tapestry ──────────────────────

def backfill_from_vault(verbose: bool = True) -> tuple[int, int]:
    """
    掃描所有已增強（有 enriched_at）的 .md 記憶，重建完整 Tapestry。
    回傳 (記憶數, 節點數)。
    """
    import yaml

    EXCLUDE = {"00_System"}
    conn     = get_conn()
    mem_count = 0

    # 清空舊資料，重新建立
    for tbl in ["mem_person", "mem_location", "mem_event", "mem_period",
                "event_loc", "person_loc", "person_event"]:
        try:
            conn.execute(f"MATCH ()-[r:{tbl}]->() DELETE r")
        except Exception:
            pass
    for tbl in ["Memory", "Person", "Location", "Event", "Period"]:
        try:
            conn.execute(f"MATCH (n:{tbl}) DELETE n")
        except Exception:
            pass

    for md_file in sorted(BASE.rglob("*.md")):
        if any(part in EXCLUDE for part in md_file.parts):
            continue
        if md_file.name in {"README.md", ".cursorrules"}:
            continue

        content = md_file.read_text(encoding="utf-8")
        if "enriched_at:" not in content:
            continue
        if not content.startswith("---"):
            continue

        end = content.find("\n---", 3)
        if end < 0:
            continue
        raw_fm = content[3:end]
        clean  = [ln for ln in raw_fm.split("\n") if not ln.strip().startswith("#")]
        try:
            fm = yaml.safe_load("\n".join(clean)) or {}
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue

        ents = fm.get("entities") or {}
        if not isinstance(ents, dict):
            ents = {}

        enrichment = {
            "entities": {
                "locations": ents.get("locations") or [],
                "people":    ents.get("people")    or [],
                "events":    ents.get("events")    or [],
            },
            "period":         fm.get("period", "") or "",
            "personal_facts": fm.get("personal_facts") or [],
        }

        rel_path = str(md_file.relative_to(BASE))
        weave_memory(conn, rel_path, enrichment)
        mem_count += 1

    # 統計
    stats = tapestry_stats(conn)
    if verbose:
        print(f"[TAPESTRY] 織入 {mem_count} 份記憶")
        print(f"[TAPESTRY] 節點：{stats['nodes']}  邊：{stats['edges']}")
        print(f"[TAPESTRY] 節點類型：{stats['by_type']}")
        print(f"[TAPESTRY] DB 路徑：{TAPESTRY_DB}")

    return mem_count, stats["nodes"]


# ─── 統計 ────────────────────────────────────────────────────

def tapestry_stats(conn: kuzu.Connection | None = None) -> dict:
    """回傳節點/邊統計資訊。"""
    _own = conn is None
    if _own:
        conn = get_conn()

    node_tables = ["Memory", "Person", "Location", "Event", "Period"]
    rel_tables  = ["mem_person", "mem_location", "mem_event", "mem_period",
                   "event_loc", "person_loc", "person_event"]

    by_type: dict[str, int] = {}
    total_nodes = 0
    for tbl in node_tables:
        try:
            r = conn.execute(f"MATCH (n:{tbl}) RETURN COUNT(n) AS c")
            c = r.get_as_df()["c"].iloc[0]
            by_type[tbl.lower()] = int(c)
            total_nodes += int(c)
        except Exception:
            by_type[tbl.lower()] = 0

    by_rel: dict[str, int] = {}
    total_edges = 0
    for tbl in rel_tables:
        try:
            r = conn.execute(f"MATCH ()-[e:{tbl}]->() RETURN COUNT(e) AS c")
            c = r.get_as_df()["c"].iloc[0]
            by_rel[tbl] = int(c)
            total_edges += int(c)
        except Exception:
            by_rel[tbl] = 0

    return {
        "nodes":   total_nodes,
        "edges":   total_edges,
        "by_type": by_type,
        "by_rel":  by_rel,
    }


# ─── CLI ────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Memosyne Tapestry — kuzu 圖拓樸")
    ap.add_argument("--backfill", action="store_true", help="從現有記憶庫重建 Tapestry")
    ap.add_argument("--stats",    action="store_true", help="顯示統計")
    ap.add_argument("--search",   type=str, default="", help="圖搜尋測試（逗號分隔關鍵詞）")
    args = ap.parse_args()

    if args.backfill:
        print("[TAPESTRY] The Grand Weaving begins — rebuilding from the Vault...")
        backfill_from_vault(verbose=True)

    elif args.stats:
        conn  = get_conn()
        stats = tapestry_stats(conn)
        print(f"[TAPESTRY] 節點：{stats['nodes']}  邊：{stats['edges']}")
        print(f"  節點類型：{stats['by_type']}")
        print(f"  邊類型：{stats['by_rel']}")

    elif args.search:
        terms = [t.strip() for t in args.search.split(",") if t.strip()]
        conn  = get_conn()
        paths = graph_search(terms, conn)
        print(f"[TAPESTRY] 搜尋：{terms}")
        if not paths:
            print("  The waters are still. No echoes found.")
        else:
            for i, path in enumerate(paths[:10], 1):
                print(f"  #{i} {path}")

    else:
        ap.print_help()
