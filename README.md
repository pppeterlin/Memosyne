# Memosyne — Personal Memory Infrastructure

> *Mnemosyne (Μνημοσύνη) — Titaness of Memory, mother of the Nine Muses.*
> *Those who drink from her spring remember everything; those who drink from Lethe forget all.*

Memosyne 是一個本地優先的個人記憶基礎設施，透過 MCP 協議讓 AI Agent（Claude、Cursor 等）能夠存取你的個人脈絡——日記、對話紀錄、Profile 等——並以認知科學的方式組織和檢索。

---

## 架構總覽

```
原始資料（.pages / .md / Gemini 導出）
    ↓ spring/（記憶之泉）
ingest.py — The Spring Ritual
    ↓
enrich.py — The Weaving（Oracle of Mneme / Ollama LLM）
    ↓
vectorize.py — The Inscription（向量化 + 索引）
    ↓
Personal_Brain_DB/（The Vault）
    ├── 10_Profile/   — Semantic Memory（語意記憶）
    ├── 20_AI_Chats/  — Working Memory（工作記憶）
    ├── 30_Journal/   — Episodic Memory（情節記憶）
    ├── 40_Projects/  — Procedural Memory（程序記憶）
    └── 50_Knowledge/ — Semantic Memory（知識積累）
```

---

## 搜尋架構

```
查詢
  ├─ Dense Vector（ChromaDB, MiniLM-L12, cosine）
  │   + Contextual Retrieval（The Illumination）
  │     每個 chunk 附加全局語境摘要，改善斷章取義問題
  ├─ BM25 關鍵字（CJK bigram tokenizer）
  ├─ Tapestry Graph（Kuzu 圖譜，實體關聯跳轉）
  └─ PPR Spreading Activation（Personalized PageRank）
       以搜尋結果為 seed，發現隱藏關聯記憶
         ↓
   RRF（Reciprocal Rank Fusion）融合
         ↓
   ACT-R 認知衰減重排（The Chronicle of Mneme）
   A_i = ln(Σ t_k^{-0.5})  — 近期且高頻的記憶優先
```

---

## 快速開始

### 環境設定

```bash
workon personal-memory   # virtualenvwrapper
pip install -r Personal_Brain_DB/00_System/requirements.txt
```

### 入庫新記憶

```bash
# 把任何格式丟進 spring/
cp 我的日記.pages spring/

# 執行 The Spring Ritual
python3 Personal_Brain_DB/00_System/ingest.py

# 其他選項
python3 Personal_Brain_DB/00_System/ingest.py --dry-run      # 預覽
python3 Personal_Brain_DB/00_System/ingest.py --no-enrich    # 跳過 LLM 增強
python3 Personal_Brain_DB/00_System/ingest.py --rebuild      # 完整重建索引
```

### 搜尋記憶

```bash
# 互動式搜尋 REPL
python3 Personal_Brain_DB/00_System/search.py

# 單次搜尋
python3 Personal_Brain_DB/00_System/vectorize.py --query "Osaka工作" --top 5

# RAG 對話（本地 Ollama）
python3 Personal_Brain_DB/00_System/chat.py
```

---

## 腳本說明

| 腳本 | 功能 | 主要選項 |
|------|------|---------|
| `ingest.py` | 格式偵測、路由、一鍵入庫 | `--dry-run`, `--no-enrich`, `--rebuild` |
| `enrich.py` | LLM 語意增強（Oracle of Mneme） | `--rebuild`, `--model`, `--file` |
| `vectorize.py` | 向量化 + BM25 索引 | `--rebuild`, `--contextualize`, `--query` |
| `tapestry.py` | 知識圖譜管理 | `--backfill`, `--stats`, `--search`, `--ppr` |
| `mcp_server.py` | MCP 伺服器（對外接口） | — |
| `search.py` | 互動式搜尋 REPL | — |
| `chat.py` | RAG 對話（Ollama + Gemini） | — |
| `augury.py` | 記憶品質審計與修正 | `--inspect`, `--correct`, `--patrol` |
| `mneme_weight.py` | ACT-R 存取紀錄與認知衰減 | `--stats`, `--top`, `--score` |
| `slumber.py` | 記憶鞏固（The Rite of Slumber） | `--reflect`, `--hebbian`, `--forget`, `--stats` |
| `watch.py` | 檔案系統監控守夜 | — |

---

## MCP 設定（Claude Desktop / Cursor）

```json
{
  "mcpServers": {
    "personal-brain": {
      "command": "/Users/yourname/.virtualenvs/personal-memory/bin/python",
      "args": [
        "/Users/yourname/Documents/Python/personal-memory/Personal_Brain_DB/00_System/mcp_server.py"
      ]
    }
  }
}
```

**MCP 工具：**
- `search_memory(query, top_k)` — 混合搜尋 + ACT-R 重排
- `get_profile(section)` — 讀取 Profile
- `list_journals(year, limit)` — 瀏覽日記清單
- `read_file(path)` — 讀取任意記憶檔案
- `optimize_memory(action)` — 觸發記憶鞏固（reflect/hebbian/forget/all）
- `get_memory_health()` — Chronicle 健康報告

---

## 認知功能

### Contextual Retrieval — The Illumination

解決切片斷章取義問題。為每個段落生成「全局語境摘要」，讓 embedding 捕捉到段落在整篇文件中的角色。

```bash
# 生成語境化摘要（首次或加入新記憶後）
python3 Personal_Brain_DB/00_System/vectorize.py --contextualize

# 生成後重建索引
python3 Personal_Brain_DB/00_System/vectorize.py --rebuild
```

快取在 `contextual_cache.json`，不重複呼叫 LLM。

### ACT-R 認知衰減 — The Chronicle of Mneme

基於認知科學的記憶重排：近期且頻繁使用的記憶排名更高。

```
A_i = ln(Σ_{k=1}^{n} t_k^{-0.5})
```

自動記錄每次搜尋存取，整合在所有搜尋路徑中。

```bash
python3 Personal_Brain_DB/00_System/mneme_weight.py --stats
python3 Personal_Brain_DB/00_System/mneme_weight.py --top 10
```

### Tapestry — 知識圖譜

實體關聯圖（Kuzu 圖資料庫），解決「Tokyo → 找不到 friend-A 相關記憶」的跨實體斷鏈問題。

節點：Memory, Person, Location, Event, Period
邊：mem_person, mem_location, mem_event, mem_period, person_loc, event_loc, person_event, co_recalled

```bash
python3 Personal_Brain_DB/00_System/tapestry.py --stats
python3 Personal_Brain_DB/00_System/tapestry.py --search "Tokyo,friend-A"
python3 Personal_Brain_DB/00_System/tapestry.py --ppr "30_Journal/2025/250604.md"
```

### The Rite of Slumber — 記憶鞏固

定期整理記憶庫：

| 子儀式 | 功能 |
|--------|------|
| **Reflection** | LLM 從近期記憶提煉洞察 → `10_Profile/reflections/` |
| **Hebbian Learning** | 共同被搜尋的記憶加強 `co_recalled` 邊 |
| **The Lethe Protocol** | importance=low 且長期未用的記憶標記 `dormant` |

```bash
python3 Personal_Brain_DB/00_System/slumber.py          # 完整鞏固
python3 Personal_Brain_DB/00_System/slumber.py --reflect
python3 Personal_Brain_DB/00_System/slumber.py --forget --dry-run
```

---

## 技術棧

| 層 | 技術 |
|----|------|
| 向量 DB | ChromaDB (cosine, HNSW) |
| Embedding | paraphrase-multilingual-MiniLM-L12-v2 (384-dim) |
| 圖資料庫 | Kuzu (Cypher, 嵌入式) |
| 關鍵字索引 | rank-bm25 (CJK bigram) |
| PPR | NetworkX pagerank |
| LLM 後端 | Ollama (本地) + Google Gemini (雲端) |
| MCP 框架 | FastMCP |
| 認知重排 | ACT-R (自實作, SQLite chronicle) |

---

## 目錄結構

```
personal-memory/
├── Personal_Brain_DB/
│   ├── 00_System/          ← 所有腳本、索引、資料庫
│   ├── 10_Profile/         ← 個人 Profile（不進 git）
│   ├── 20_AI_Chats/        ← AI 對話紀錄（不進 git）
│   ├── 30_Journal/         ← 日記手札（不進 git）
│   ├── 40_Projects/        ← 專案筆記（不進 git）
│   └── 50_Knowledge/       ← 知識文件（不進 git）
├── spring/                 ← 記憶之泉（drop zone）
├── _internal/              ← 開發文檔（不進 git）
├── CLAUDE.md               ← 專案開發規範
└── README.md
```

*所有個人記憶內容（10~50 資料夾）均在 `.gitignore` 中排除。*

---

*最後更新：2026-04-11*
