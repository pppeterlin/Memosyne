# Memosyne — 個人記憶基礎設施

[English](README.md)

> *Mnemosyne（Μνημοσύνη）— 泰坦神記憶女神，九位繆思女神之母。*
> *飲此泉水者，靈魂記憶永不遺失；飲忘川之水者，遺忘一切。*

Memosyne 是一個本地優先的個人記憶基礎設施，透過 MCP 協議讓 AI Agent（Claude、Cursor 等）能夠存取你的個人脈絡——日記、對話紀錄、Profile 等——並以認知科學的方式組織和檢索。

## 核心精神

AI 能力每季都在跳躍，但再強的模型也無法在冷啟動狀態下認識「你」——你和誰共度時光、做過哪些決定、如何一路改變——這些只散落在日記、對話與筆記裡。

**現在就該開始累積個人記憶庫，趕在 agent 時代全面到來之前。** 今天建立的記憶可以接到未來任何模型上，讓 agent 做出真正屬於你的決策，而非通用建議。

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
python -m venv .venv
source .venv/bin/activate
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
# v0.3 command surface
python3 memosyne.py health
python3 memosyne.py search "測試查詢" --top 5

# 互動式搜尋 REPL
python3 Personal_Brain_DB/00_System/search.py

# 單次搜尋
python3 Personal_Brain_DB/00_System/vectorize.py --query "深圳工作" --top 5

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

### v0.3 Command Surface

v0.3 CLI 先作為既有腳本的薄包裝：

```bash
python3 memosyne.py init
python3 memosyne.py ingest
python3 memosyne.py search "測試查詢"
python3 memosyne.py rebuild
python3 memosyne.py health
python3 memosyne.py mcp --check
python3 memosyne.py slumber --stats
python3 memosyne.py chronicle --stats
```

若使用 editable install，則可直接執行：

```bash
pip install -e .
memosyne health
```

日常操作與 troubleshooting 見 [docs/operations.md](docs/operations.md)。
設定說明見 [docs/configuration.md](docs/configuration.md)，MCP 設定見 [docs/mcp.md](docs/mcp.md)。

---

## MCP 設定（Claude Desktop / Cursor）

```json
{
  "mcpServers": {
    "personal-brain": {
      "command": "/path/to/your/venv/bin/python",
      "args": [
        "/path/to/memosyne/Personal_Brain_DB/00_System/mcp_server.py"
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
python3 Personal_Brain_DB/00_System/vectorize.py --contextualize
python3 Personal_Brain_DB/00_System/vectorize.py --rebuild
```

快取在 `contextual_cache.json`，不重複呼叫 LLM。

### ACT-R 認知衰減 — The Chronicle of Mneme

基於認知科學的記憶重排：近期且頻繁使用的記憶排名更高。
`chronicle.jsonl` 是 append-only 真相來源，`chronicle.db` 是可由 JSONL 重建的 SQLite 查詢快取。

```
A_i = ln(Σ_{k=1}^{n} t_k^{-0.5})
```

```bash
python3 Personal_Brain_DB/00_System/mneme_weight.py --stats
python3 Personal_Brain_DB/00_System/mneme_weight.py --top 10
python3 Personal_Brain_DB/00_System/mneme_weight.py --export-jsonl --replace-jsonl
python3 Personal_Brain_DB/00_System/mneme_weight.py --rebuild-db-from-jsonl
```

### Tapestry — 知識圖譜

實體關聯圖（Kuzu 圖資料庫），解決跨實體斷鏈問題（例如：「外婆家」→「暑假回憶」）。

```bash
python3 Personal_Brain_DB/00_System/tapestry.py --stats
python3 Personal_Brain_DB/00_System/tapestry.py --search "外婆家,暑假回憶"
python3 Personal_Brain_DB/00_System/tapestry.py --ppr "30_Journal/2025/250604.md"
```

### The Rite of Slumber — 記憶鞏固

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
memosyne/
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

## 致謝與參考文獻（Acknowledgements & References）

Memosyne 站在許多優秀研究與開源專案的肩膀上。以下是架構中採用的技術、以及 [優化方案_索引與保存管理.md](優化方案_索引與保存管理.md) 規劃的 v2 升級所依據的文獻。該有的致敬不能少。

### 已實作技術的基礎文獻

- **ACT-R 認知衰減** — Anderson, J. R. 等. *An integrated theory of the mind.* Psychological Review (2004)。基礎激活公式 `B_i = ln(Σ t_k^{-d})` 驅動 The Chronicle of Mneme。
- **Contextual Retrieval** — Anthropic (2024). [Introducing Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)。啟發 The Illumination。
- **Reciprocal Rank Fusion (RRF)** — Cormack 等, SIGIR (2009)。
- **BM25** — Robertson & Zaragoza, Foundations and Trends in IR (2009)。
- **Personalized PageRank** — Haveliwala, WWW (2002)。Tapestry 擴散激發的核心演算法。
- **HippoRAG** — Gutiérrez 等. [*HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs.*](https://arxiv.org/abs/2405.14831) NeurIPS (2024)。

### Retrieval v2 參考研究

- **HippoRAG 2** — [arxiv 2502.14802](https://arxiv.org/abs/2502.14802) (2025)。短語+段落統一圖 PPR。
- **Zep / Graphiti** — [arxiv 2501.13956](https://arxiv.org/abs/2501.13956) (2025)。雙時序邊與邊失效機制。開源：[getzep/graphiti](https://github.com/getzep/graphiti)。
- **Mem0** — [arxiv 2504.19413](https://arxiv.org/abs/2504.19413) (2025)。CRUD 式記憶操作與衝突解決。
- **A-MEM** — [arxiv 2502.12110](https://arxiv.org/abs/2502.12110) (2025)。Zettelkasten 式記憶演化。
- **LightRAG** — [arxiv 2410.05779](https://arxiv.org/abs/2410.05779) (2024)。增量式雙層級圖更新。
- **GraphRAG** — [arxiv 2404.16130](https://arxiv.org/abs/2404.16130) Microsoft Research (2024)。
- **HyDE** — [arxiv 2212.10496](https://arxiv.org/abs/2212.10496) ACL (2023)。The Triple Echo 的 HyQE 視角依據。
- **Self-RAG** — [arxiv 2310.11511](https://arxiv.org/abs/2310.11511) ICLR (2024)。The Mirror of Truth 的依據。
- **ColBERT / ColBERTv2** — [arxiv 2004.12832](https://arxiv.org/abs/2004.12832) SIGIR (2020)。
- **MemGPT / Letta** — [arxiv 2310.08560](https://arxiv.org/abs/2310.08560) (2023)。
- **LongMemEval** — [arxiv 2410.10813](https://arxiv.org/abs/2410.10813) (2024)。The Augury Benchmark 的評估方法論。
- **RAGAS** — [docs.ragas.io](https://docs.ragas.io) EACL (2024)。

### 工具與函式庫

- [ChromaDB](https://www.trychroma.com/) · [Kuzu](https://kuzudb.com/) · [NetworkX](https://networkx.org/) · [rank-bm25](https://github.com/dorianbrown/rank_bm25) · [sentence-transformers](https://www.sbert.net/) · [FastMCP](https://github.com/jlowin/fastmcp) · [Ollama](https://ollama.com/)

若有未列出的引用來源，歡迎開 issue 補上 —— 引用不是裝飾，是該做的事。

---

*最後更新：2026-04-21*
