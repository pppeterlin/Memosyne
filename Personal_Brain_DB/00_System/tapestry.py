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
  python3 tapestry.py --search "Tokyo"   # 圖搜尋測試
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import kuzu
try:
    from artifacts import artifact_path
except ImportError:
    def artifact_path(name: str) -> Path:
        mapping = {"tapestry_db": "tapestry_db"}
        return Path(__file__).parent / mapping.get(name, name)

# ─── Bi-temporal edge properties ─────────────────────────────
# t_valid_start   : 關係在真實世界開始成立的時間（通常 = t_ingested）
# t_valid_end     : 關係失效時間（None/NULL 表示目前仍有效）
# t_ingested      : 寫入系統的時間
# invalidated_by  : 被哪條記憶 path 所觸發的失效
_REL_TABLES = [
    "mem_person", "mem_location", "mem_event", "mem_period",
    "event_loc",  "person_loc",   "person_event",
]
_TEMPORAL_COLUMNS = [
    ("t_valid_start",  "TIMESTAMP"),
    ("t_valid_end",    "TIMESTAMP"),
    ("t_ingested",     "TIMESTAMP"),
    ("invalidated_by", "STRING"),
]

BASE          = Path(__file__).parent.parent
TAPESTRY_DB   = artifact_path("tapestry_db").resolve()

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
        "CREATE NODE TABLE IF NOT EXISTS Person(name STRING, aliases STRING[], PRIMARY KEY(name))",
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

    # Migration: add aliases column to existing Person table
    try:
        conn.execute("ALTER TABLE Person ADD aliases STRING[] DEFAULT []")
    except Exception:
        pass  # column already exists

    # Migration: The Two Rivers — bi-temporal properties on every REL table
    for rel in _REL_TABLES:
        for col, ctype in _TEMPORAL_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE {rel} ADD {col} {ctype}")
            except Exception:
                pass  # column already exists


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


# ─── Person 節點管理（The Naming Rite 用）─────────────────────

def get_all_persons(conn: kuzu.Connection | None = None) -> list[dict]:
    """回傳所有 Person 節點 [{name, aliases}, ...]。"""
    _own = conn is None
    if _own:
        conn = get_conn()
    try:
        r = conn.execute("MATCH (p:Person) RETURN p.name AS name, p.aliases AS aliases")
        df = r.get_as_df()
        results = []
        for _, row in df.iterrows():
            aliases = row["aliases"] if row["aliases"] is not None else []
            if not isinstance(aliases, list):
                aliases = []
            results.append({"name": row["name"], "aliases": aliases})
        return results
    except Exception:
        return []


def get_alias_map(conn: kuzu.Connection | None = None) -> dict[str, str]:
    """
    建立 alias → canonical_name 的查找表。
    包含正規化後的名稱（lowercase / 去連字號 / 去底線 / strip）。
    """
    persons = get_all_persons(conn)
    alias_map: dict[str, str] = {}
    for p in persons:
        canonical = p["name"]
        # 自身的正規化形式也加入
        alias_map[canonical.lower().replace("-", "").replace("_", "").strip()] = canonical
        for alias in p["aliases"]:
            if isinstance(alias, str) and alias:
                alias_map[alias.lower().replace("-", "").replace("_", "").strip()] = canonical
                alias_map[alias] = canonical
    return alias_map


def _person_edge_count(conn: kuzu.Connection, name: str) -> int:
    """計算某 Person 節點的總邊數（用於選擇 canonical name）。"""
    count = 0
    for query in [
        "MATCH (:Memory)-[r:mem_person]->(p:Person {name: $n}) RETURN COUNT(r) AS c",
        "MATCH (p:Person {name: $n})-[r:person_loc]->(:Location) RETURN COUNT(r) AS c",
        "MATCH (p:Person {name: $n})-[r:person_event]->(:Event) RETURN COUNT(r) AS c",
    ]:
        try:
            r = conn.execute(query, {"n": name})
            count += int(r.get_as_df()["c"].iloc[0])
        except Exception:
            continue
    return count


def merge_persons(
    conn: kuzu.Connection,
    canonical: str,
    to_merge: list[str],
) -> int:
    """
    將 to_merge 中的 Person 節點合併到 canonical。
    邊重導 → aliases 更新 → 刪除舊節點。
    回傳重導的邊數。
    """
    redirected = 0

    # 確保 canonical 節點存在
    _merge_node(conn, "Person", canonical)

    for alias_name in to_merge:
        if alias_name == canonical:
            continue

        # ── 重導 mem_person 邊（Memory → Person）──────────────
        try:
            r = conn.execute(
                "MATCH (m:Memory)-[:mem_person]->(p:Person {name: $old}) "
                "RETURN m.path AS mp", {"old": alias_name}
            )
            for mp in r.get_as_df()["mp"].tolist():
                conn.execute(
                    "MATCH (m:Memory {path: $mp}), (p:Person {name: $new}) "
                    "MERGE (m)-[:mem_person]->(p)",
                    {"mp": mp, "new": canonical}
                )
                redirected += 1
        except Exception:
            pass

        # ── 重導 person_loc 邊（Person → Location）───────────
        try:
            r = conn.execute(
                "MATCH (p:Person {name: $old})-[r:person_loc]->(l:Location) "
                "RETURN l.name AS ln, r.evidence AS ev", {"old": alias_name}
            )
            df = r.get_as_df()
            for _, row in df.iterrows():
                ev = row["ev"] if row["ev"] else ""
                conn.execute(
                    "MATCH (p:Person {name: $new}), (l:Location {name: $ln}) "
                    "MERGE (p)-[:person_loc {evidence: $ev}]->(l)",
                    {"new": canonical, "ln": row["ln"], "ev": ev}
                )
                redirected += 1
        except Exception:
            pass

        # ── 重導 person_event 邊（Person → Event）────────────
        try:
            r = conn.execute(
                "MATCH (p:Person {name: $old})-[:person_event]->(e:Event) "
                "RETURN e.name AS en", {"old": alias_name}
            )
            for en in r.get_as_df()["en"].tolist():
                conn.execute(
                    "MATCH (p:Person {name: $new}), (e:Event {name: $en}) "
                    "MERGE (p)-[:person_event]->(e)",
                    {"new": canonical, "en": en}
                )
                redirected += 1
        except Exception:
            pass

        # ── 刪除舊節點的所有邊，再刪節點 ─────────────────────
        for rel in ["mem_person", "person_loc", "person_event"]:
            try:
                conn.execute(
                    f"MATCH (p:Person {{name: $old}})-[r:{rel}]-() DELETE r",
                    {"old": alias_name}
                )
            except Exception:
                pass
            try:
                conn.execute(
                    f"MATCH ()-[r:{rel}]->(p:Person {{name: $old}}) DELETE r",
                    {"old": alias_name}
                )
            except Exception:
                pass
        try:
            conn.execute("MATCH (p:Person {name: $old}) DELETE p", {"old": alias_name})
        except Exception:
            pass

    # ── 更新 canonical 的 aliases ─────────────────────────────
    existing = []
    try:
        r = conn.execute(
            "MATCH (p:Person {name: $n}) RETURN p.aliases AS a", {"n": canonical}
        )
        a = r.get_as_df()["a"].iloc[0]
        if isinstance(a, list):
            existing = a
    except Exception:
        pass

    all_aliases = list(set(existing + to_merge))
    if canonical in all_aliases:
        all_aliases.remove(canonical)

    try:
        conn.execute(
            "MATCH (p:Person {name: $n}) SET p.aliases = $a",
            {"n": canonical, "a": all_aliases}
        )
    except Exception:
        pass

    return redirected


# ─── 主要織入函式 ─────────────────────────────────────────────

def weave_memory(
    conn: kuzu.Connection,
    memory_path: str,
    enrichment: dict,
    now: datetime | None = None,
) -> None:
    """
    The Weaving — 將一份記憶的 enrichment 結果織入 Tapestry。

    呼叫時機：enrich.py 每次成功增強後立即呼叫。
    conn 傳入已開啟的 Connection（由呼叫方管理生命週期）。

    Bi-temporal：新建的邊會設定 t_valid_start = t_ingested = now。
                 既有邊的時間戳用 ON CREATE SET 保護，不被覆蓋。
    """
    entities       = enrichment.get("entities", {})
    period         = enrichment.get("period", "") or ""
    locations      = [l for l in entities.get("locations", []) if l]
    people         = [p for p in entities.get("people",    []) if p]
    events         = [e for e in entities.get("events",    []) if e]
    personal_facts = enrichment.get("personal_facts", []) or []

    ts = now or datetime.now()

    # ── Memory node ───────────────────────────────────────────
    _merge_memory(conn, memory_path)

    # ── Period ────────────────────────────────────────────────
    if period:
        _merge_node(conn, "Period", period)
        conn.execute("""
            MATCH (m:Memory {path: $mp}), (p:Period {name: $pn})
            MERGE (m)-[r:mem_period]->(p)
            ON CREATE SET r.t_valid_start = $ts, r.t_ingested = $ts
        """, {"mp": memory_path, "pn": period, "ts": ts})

    # ── Locations ─────────────────────────────────────────────
    for loc in locations:
        _merge_node(conn, "Location", loc)
        conn.execute("""
            MATCH (m:Memory {path: $mp}), (l:Location {name: $ln})
            MERGE (m)-[r:mem_location]->(l)
            ON CREATE SET r.t_valid_start = $ts, r.t_ingested = $ts
        """, {"mp": memory_path, "ln": loc, "ts": ts})

    # ── People ────────────────────────────────────────────────
    for person in people:
        _merge_node(conn, "Person", person)
        conn.execute("""
            MATCH (m:Memory {path: $mp}), (p:Person {name: $pn})
            MERGE (m)-[r:mem_person]->(p)
            ON CREATE SET r.t_valid_start = $ts, r.t_ingested = $ts
        """, {"mp": memory_path, "pn": person, "ts": ts})

    # ── Events ────────────────────────────────────────────────
    for event in events:
        _merge_node(conn, "Event", event)
        conn.execute("""
            MATCH (m:Memory {path: $mp}), (e:Event {name: $en})
            MERGE (m)-[r:mem_event]->(e)
            ON CREATE SET r.t_valid_start = $ts, r.t_ingested = $ts
        """, {"mp": memory_path, "en": event, "ts": ts})
        # Event → Location
        for loc in locations:
            conn.execute("""
                MATCH (e:Event {name: $en}), (l:Location {name: $ln})
                MERGE (e)-[r:event_loc]->(l)
                ON CREATE SET r.t_valid_start = $ts, r.t_ingested = $ts
            """, {"en": event, "ln": loc, "ts": ts})
        # Person → Event
        for person in people:
            conn.execute("""
                MATCH (p:Person {name: $pn}), (e:Event {name: $en})
                MERGE (p)-[r:person_event]->(e)
                ON CREATE SET r.t_valid_start = $ts, r.t_ingested = $ts
            """, {"pn": person, "en": event, "ts": ts})

    # ── personal_facts → Person located_in Location ───────────
    for fact in personal_facts:
        if not isinstance(fact, str):
            continue
        for person in people:
            for loc in locations:
                if person in fact and loc in fact:
                    conn.execute("""
                        MATCH (p:Person {name: $pn}), (l:Location {name: $ln})
                        MERGE (p)-[r:person_loc {evidence: $ev}]->(l)
                        ON CREATE SET r.t_valid_start = $ts, r.t_ingested = $ts
                    """, {"pn": person, "ln": loc, "ev": fact[:80], "ts": ts})


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


# ─── PPR Spreading Activation（傳播激發檢索）─────────────────
#
# Personalized PageRank：以搜尋結果為 seed，在圖譜中擴散，
# 發現語義隱含相關但未被向量/BM25 直接命中的記憶。
#
# 流程：Kuzu 子圖 → NetworkX DiGraph → PPR → 排序

def _extract_subgraph_to_nx(conn: kuzu.Connection):
    """
    從 Kuzu 提取完整圖到 NetworkX DiGraph。
    節點格式："{type}::{name}" (e.g. "Memory::30_Journal/2025/250604.md")
    """
    import networkx as nx

    G = nx.DiGraph()

    # 提取所有節點
    for label, key in [("Memory", "path"), ("Person", "name"),
                       ("Location", "name"), ("Event", "name"), ("Period", "name")]:
        try:
            result = conn.execute(f"MATCH (n:{label}) RETURN n.{key} AS k")
            df = result.get_as_df()
            for val in df["k"].tolist():
                G.add_node(f"{label}::{val}", type=label, name=val)
        except Exception:
            continue

    # 提取所有邊
    rel_queries = [
        ("mem_person",   "Memory", "path", "Person",   "name"),
        ("mem_location", "Memory", "path", "Location", "name"),
        ("mem_event",    "Memory", "path", "Event",    "name"),
        ("mem_period",   "Memory", "path", "Period",   "name"),
        ("event_loc",    "Event",  "name", "Location", "name"),
        ("person_loc",   "Person", "name", "Location", "name"),
        ("person_event", "Person", "name", "Event",    "name"),
    ]
    for rel, from_label, from_key, to_label, to_key in rel_queries:
        try:
            result = conn.execute(
                f"MATCH (a:{from_label})-[:{rel}]->(b:{to_label}) "
                f"RETURN a.{from_key} AS src, b.{to_key} AS dst"
            )
            df = result.get_as_df()
            for _, row in df.iterrows():
                src_id = f"{from_label}::{row['src']}"
                dst_id = f"{to_label}::{row['dst']}"
                # 雙向邊，讓 PPR 能雙向擴散
                G.add_edge(src_id, dst_id)
                G.add_edge(dst_id, src_id)
        except Exception:
            continue

    return G


def spreading_activation(
    seed_paths: list[str],
    conn: kuzu.Connection | None = None,
    top_k: int = 15,
    alpha: float = 0.15,
    seed_entities: list[str] | None = None,
) -> list[tuple[str, float]]:
    """
    PPR Spreading Activation — HippoRAG 2 升級版。

    同時接受 passage node（Memory）和 phrase node（Person/Location/Event）
    作為 seed，在圖譜中擴散。擴散後只取 Memory 節點分數。

    Args:
        seed_paths: 種子記憶路徑（通常是向量搜尋的 top-K）
        conn: Kuzu connection（可選，不提供則自動建立）
        top_k: 回傳前 K 個結果
        alpha: PPR damping（0.15 = 85% 機率繼續擴散）
        seed_entities: 種子實體名稱（HippoRAG 2：phrase node seeds）

    Returns:
        [(memory_path, ppr_score), ...] — 排除 seed 自身
    """
    import networkx as nx

    _own = conn is None
    if _own:
        if not TAPESTRY_DB.exists():
            return []
        conn = get_conn()

    G = _extract_subgraph_to_nx(conn)
    if G.number_of_nodes() == 0:
        return []

    # 建立 personalization 向量：passage + phrase seeds
    personalization = {}

    # Passage seeds（Memory 節點）
    for path in seed_paths:
        node_id = f"Memory::{path}"
        if node_id in G:
            personalization[node_id] = 1.0

    # Phrase seeds（Entity 節點）— HippoRAG 2
    if seed_entities:
        for entity in seed_entities:
            # 嘗試所有實體類型
            for label in ("Person", "Location", "Event", "Period"):
                node_id = f"{label}::{entity}"
                if node_id in G:
                    personalization[node_id] = 1.0
                    break

    if not personalization:
        return []

    # 執行 Personalized PageRank
    try:
        ppr_scores = nx.pagerank(G, alpha=alpha, personalization=personalization,
                                 max_iter=100, tol=1e-6)
    except nx.PowerIterationFailedConvergence:
        ppr_scores = nx.pagerank(G, alpha=alpha, personalization=personalization,
                                 max_iter=200, tol=1e-4)

    # 過濾：只取 Memory 節點，排除 seed 自身
    seed_set = set(f"Memory::{p}" for p in seed_paths)
    memory_scores = [
        (node_id.split("::", 1)[1], score)
        for node_id, score in ppr_scores.items()
        if node_id.startswith("Memory::") and node_id not in seed_set
    ]

    memory_scores.sort(key=lambda x: -x[1])
    return memory_scores[:top_k]


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


# ─── The Two Rivers — bi-temporal helpers ───────────────────

def backfill_temporal(conn: kuzu.Connection | None = None,
                      verbose: bool = True) -> dict[str, int]:
    """
    既有邊回填 t_valid_start = t_ingested = now（t_valid_end 保持 NULL）。
    已有 t_ingested 的邊不動。
    """
    _own = conn is None
    if _own:
        conn = get_conn()
    now = datetime.now()
    touched: dict[str, int] = {}
    for rel in _REL_TABLES:
        try:
            r = conn.execute(
                f"MATCH ()-[e:{rel}]->() WHERE e.t_ingested IS NULL "
                f"SET e.t_valid_start = $ts, e.t_ingested = $ts "
                f"RETURN COUNT(e) AS c",
                {"ts": now},
            )
            touched[rel] = int(r.get_as_df()["c"].iloc[0])
        except Exception as e:
            touched[rel] = 0
            if verbose:
                print(f"  [backfill] {rel} failed: {e}")
    if verbose:
        total = sum(touched.values())
        print(f"[TAPESTRY] Backfilled {total} edges with timestamps.")
        for k, v in touched.items():
            if v:
                print(f"    {k}: {v}")
    return touched


def invalidate_edge(conn: kuzu.Connection, rel: str,
                    from_label: str, from_name: str,
                    to_label: str,   to_name: str,
                    invalidated_by: str,
                    when: datetime | None = None) -> int:
    """
    標記一條邊失效（不刪除）。用於 3.2 Ordeal。
    回傳影響邊數。
    """
    if rel not in _REL_TABLES:
        raise ValueError(f"unknown rel table: {rel}")
    ts = when or datetime.now()
    from_key = "path" if from_label == "Memory" else "name"
    to_key   = "path" if to_label   == "Memory" else "name"
    r = conn.execute(
        f"MATCH (a:{from_label} {{{from_key}: $a}})-[e:{rel}]->(b:{to_label} {{{to_key}: $b}}) "
        f"WHERE e.t_valid_end IS NULL "
        f"SET e.t_valid_end = $ts, e.invalidated_by = $by "
        f"RETURN COUNT(e) AS c",
        {"a": from_name, "b": to_name, "ts": ts, "by": invalidated_by},
    )
    return int(r.get_as_df()["c"].iloc[0])


def currently_valid_edges(conn: kuzu.Connection | None = None,
                          rel: str = "mem_person") -> list[dict]:
    """
    回傳目前有效（t_valid_end IS NULL）的邊。
    """
    _own = conn is None
    if _own:
        conn = get_conn()
    if rel not in _REL_TABLES:
        return []
    from_label, to_label = _rel_endpoints(rel)
    from_key = "path" if from_label == "Memory" else "name"
    to_key   = "path" if to_label   == "Memory" else "name"
    r = conn.execute(
        f"MATCH (a:{from_label})-[e:{rel}]->(b:{to_label}) "
        f"WHERE e.t_valid_end IS NULL "
        f"RETURN a.{from_key} AS a, b.{to_key} AS b, "
        f"       e.t_valid_start AS tvs, e.t_ingested AS tin"
    )
    df = r.get_as_df()
    return df.to_dict(orient="records")


def edges_as_of(conn: kuzu.Connection, rel: str, ts: datetime) -> list[dict]:
    """
    回傳在 ts 時間點仍然有效的邊：t_valid_start <= ts AND (t_valid_end IS NULL OR t_valid_end > ts)。
    """
    if rel not in _REL_TABLES:
        return []
    from_label, to_label = _rel_endpoints(rel)
    from_key = "path" if from_label == "Memory" else "name"
    to_key   = "path" if to_label   == "Memory" else "name"
    r = conn.execute(
        f"MATCH (a:{from_label})-[e:{rel}]->(b:{to_label}) "
        f"WHERE e.t_valid_start <= $ts "
        f"  AND (e.t_valid_end IS NULL OR e.t_valid_end > $ts) "
        f"RETURN a.{from_key} AS a, b.{to_key} AS b, "
        f"       e.t_valid_start AS tvs, e.t_valid_end AS tve",
        {"ts": ts},
    )
    return r.get_as_df().to_dict(orient="records")


def get_entity_timeline(entity_name: str,
                        conn: kuzu.Connection | None = None) -> list[dict]:
    """
    回傳某實體（Person/Location/Event）涉及的所有邊的時間線。
    結果依 t_valid_start 升序。
    """
    _own = conn is None
    if _own:
        conn = get_conn()

    # 試著識別節點類型
    label = None
    for candidate in ("Person", "Location", "Event", "Period"):
        r = conn.execute(
            f"MATCH (n:{candidate} {{name: $n}}) RETURN COUNT(n) AS c",
            {"n": entity_name},
        )
        if int(r.get_as_df()["c"].iloc[0]) > 0:
            label = candidate
            break
    if not label:
        return []

    results: list[dict] = []
    for rel in _REL_TABLES:
        from_label, to_label = _rel_endpoints(rel)
        if label not in (from_label, to_label):
            continue
        from_key = "path" if from_label == "Memory" else "name"
        to_key   = "path" if to_label   == "Memory" else "name"
        # 根據方向決定以哪一端作為篩選
        if from_label == label:
            r = conn.execute(
                f"MATCH (a:{from_label} {{name: $n}})-[e:{rel}]->(b:{to_label}) "
                f"RETURN a.{from_key} AS a, b.{to_key} AS b, "
                f"       e.t_valid_start AS tvs, e.t_valid_end AS tve, "
                f"       e.t_ingested AS tin, e.invalidated_by AS inv",
                {"n": entity_name},
            )
        else:
            r = conn.execute(
                f"MATCH (a:{from_label})-[e:{rel}]->(b:{to_label} {{name: $n}}) "
                f"RETURN a.{from_key} AS a, b.{to_key} AS b, "
                f"       e.t_valid_start AS tvs, e.t_valid_end AS tve, "
                f"       e.t_ingested AS tin, e.invalidated_by AS inv",
                {"n": entity_name},
            )
        for row in r.get_as_df().to_dict(orient="records"):
            row["rel"] = rel
            results.append(row)

    results.sort(key=lambda x: x.get("tvs") or datetime.min)
    return results


def _rel_endpoints(rel: str) -> tuple[str, str]:
    """回傳 (from_label, to_label)。"""
    mapping = {
        "mem_person":   ("Memory", "Person"),
        "mem_location": ("Memory", "Location"),
        "mem_event":    ("Memory", "Event"),
        "mem_period":   ("Memory", "Period"),
        "event_loc":    ("Event",  "Location"),
        "person_loc":   ("Person", "Location"),
        "person_event": ("Person", "Event"),
    }
    return mapping[rel]


# ─── CLI ────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Memosyne Tapestry — kuzu 圖拓樸")
    ap.add_argument("--backfill", action="store_true", help="從現有記憶庫重建 Tapestry")
    ap.add_argument("--stats",    action="store_true", help="顯示統計")
    ap.add_argument("--search",   type=str, default="", help="圖搜尋測試（逗號分隔關鍵詞）")
    ap.add_argument("--ppr",      type=str, default="", help="PPR 擴散測試（逗號分隔 memory path）")
    ap.add_argument("--backfill-temporal", action="store_true",
                    help="The Two Rivers — 既有邊回填時間戳")
    ap.add_argument("--timeline", type=str, default="",
                    help="查詢某實體（Person/Location/Event）的時間線")
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

    elif args.backfill_temporal:
        print("[TAPESTRY] The Two Rivers begin to flow — backfilling timestamps...")
        backfill_temporal(verbose=True)

    elif args.timeline:
        entries = get_entity_timeline(args.timeline)
        print(f"[TAPESTRY] Timeline for {args.timeline!r} — {len(entries)} edges:")
        for e in entries[:50]:
            tvs = e.get("tvs")
            tve = e.get("tve") or "…"
            inv = e.get("inv") or "-"
            print(f"  {tvs} → {tve}  [{e['rel']}] {e['a']} → {e['b']}  inv_by={inv}")

    elif args.ppr:
        seeds = [t.strip() for t in args.ppr.split(",") if t.strip()]
        results = spreading_activation(seeds, top_k=10)
        print(f"[TAPESTRY] PPR Spreading Activation from {len(seeds)} seeds:")
        if not results:
            print("  The waters are still. No echoes found.")
        else:
            for i, (path, score) in enumerate(results, 1):
                print(f"  #{i} PPR={score:.6f}  {path}")

    else:
        ap.print_help()
