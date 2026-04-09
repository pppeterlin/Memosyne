# Personal Brain DB

我的個人記憶資料庫。所有 AI 對話、手札、Profile 集中存放，支援語義搜尋，可掛載為 MCP Server 讓 Agent 存取。

---

## 目錄結構

```
Personal_Brain_DB/
├── README.md                      ← 本文件
├── .cursorrules                   ← Cursor/AI context 注入規則
│
├── 00_System/                     ← 系統腳本與索引（不含記憶內容）
│   ├── process_files.py           ← 主處理腳本（歸檔 + 索引）
│   ├── vectorize.py               ← 向量化腳本（ChromaDB）
│   ├── mcp_server.py              ← MCP Server（供 Agent 搜尋）
│   ├── watch.py                   ← 檔案監控守護程式
│   ├── index.json                 ← 全文語義索引（JSON）
│   ├── chroma_db/                 ← ChromaDB 向量資料庫（gitignore）
│   ├── requirements.txt           ← 依賴套件清單
│   └── templates/                 ← Markdown 模板
│
├── 10_Profile/                    ← 個人 Profile（手工維護）
│   ├── bio.md                     ← 基本資料
│   ├── career.md                  ← 職涯歷史與技術能力
│   ├── family_pets.md             ← 家庭、寵物、感情
│   └── preferences.md             ← 個人偏好與 AI 互動風格
│
├── 20_AI_Chats/
│   ├── Gemini/                    ← 從 gemini chat/ 自動歸檔
│   └── Claude/                    ← 未來可擴充
│
├── 30_Journal/                    ← 手札（從 notes/ 自動轉換）
│   ├── 2022/ 2023/ 2024/ 2025/ 2026/
│
├── 40_Projects/                   ← 進行中專案筆記（手工新增）
└── 50_Knowledge/                  ← 技術/知識筆記（手工新增）
```

---

## 記憶更新：統一指令

> **虛擬環境**：`workon personal-memory`

### 完整更新（最常用）

```bash
workon personal-memory
python3 00_System/process_files.py && python3 00_System/vectorize.py
```

這一行做完：
1. 掃描 `../gemini chat/` → 歸檔到 `20_AI_Chats/Gemini/`（增量，已存在跳過）
2. 掃描 `../notes/` → 轉換 `.pages` → `30_Journal/YYYY/` （增量）
3. 重建 `00_System/index.json`
4. 更新 ChromaDB 向量索引（增量）

### 強制重新處理所有檔案

```bash
python3 00_System/process_files.py --all && python3 00_System/vectorize.py --rebuild
```

### 只重建索引（不重新解析原始檔）

```bash
python3 00_System/process_files.py --reindex
python3 00_System/vectorize.py --rebuild
```

### 搜尋測試

```bash
python3 00_System/vectorize.py --query "Osaka工作" --top 5
python3 00_System/vectorize.py --query "奶茶芙蓉" --top 3
```

---

## 如何新增記憶

### A. Gemini 對話（自動）

1. 將匯出的 `.md` 對話檔放入 `../gemini chat/`
2. 執行 `python3 00_System/process_files.py`

### B. 手札（自動）

1. 在 Apple Pages 寫好後，存到 `../notes/`（命名格式 `YYMMDD.pages`）
2. 執行 `python3 00_System/process_files.py`

### C. 手工筆記（直接新增 .md）

可直接在 `40_Projects/` 或 `50_Knowledge/` 新增 `.md`，加上 frontmatter：

```yaml
---
uuid: ""            # 可留空，下次 reindex 不影響
title: "標題"
date_created: 2026-04-09
date_updated: 2026-04-09
type: "note"        # note | chat | bio | project
tags: ["標籤"]
summary: "一句話摘要"
---
```

然後執行 `python3 00_System/vectorize.py` 更新向量索引。

### D. 持續自動監控（背景）

```bash
pip install watchdog   # 已安裝可跳過
python3 00_System/watch.py &
```

有新檔案進 `gemini chat/` 或 `notes/` 時自動觸發處理。

---

## 向量化設計（第一原理）

> **為什麼不做關鍵詞提取？**
> 關鍵詞提取是稀疏檢索（BM25）時代的做法。現代 dense embedding 已能捕捉語義、
> 處理同義詞和跨語言匹配，單獨抽出關鍵詞去 embed 反而會失去上下文，讓向量方向跑偏。

真正影響精準度的三件事：

### 1. Metadata 注入到 chunk 文字

不把 type/date/title 只存進 filter 欄位，而是 **prepend 到 chunk 文字本身**：

```
[手札][2026-02-03][寵物] 今天是小貓回家的第一天...
```

→ embedding 向量本身就帶有「類型、時間、主題」語義，搜「手札裡的貓」能精準命中。

### 2. 語義切段（段落為單位，不是字數）

用 `\n\n`（空行）為邊界切段，保留完整段落語義。
字數硬切會把一個完整的想法切斷，扭曲語義向量方向。

### 3. 雙粒度索引

每個文件建立兩種 chunks：

| Chunk 類型 | 內容 | 適合的查詢 |
|-----------|------|-----------|
| `summary` | title + tags + summary（整篇摘要）| 「這件事我有沒有記錄過？」|
| `paragraph` | 單一段落（帶 metadata 前綴）| 「那件事的具體內容是什麼？」|

搜尋時預設對 `paragraph` chunks 搜尋，去除同一文件的重複結果。

### 架構圖

```
Markdown 文件（frontmatter + body）
    ↓ parse_frontmatter()
    ├─ summary chunk:  "[type][date][tags] title\nsummary"
    └─ paragraph chunks: "[type][date][tags] title:\n{段落文字}"
         ↓ SentenceTransformer: paraphrase-multilingual-MiniLM-L12-v2
ChromaDB PersistentClient（本地，無需外部服務）
    ↓ cosine similarity
MCP Server tools / CLI --query
```

- **模型**：`paraphrase-multilingual-MiniLM-L12-v2`，首次執行自動下載（~420MB）
- **儲存**：`00_System/chroma_db/`（已加入 .gitignore）
- **去重**：搜尋結果依文件去重，不回傳同一篇的多個段落

---

## MCP Server 設定

讓 Claude Desktop 或 Cursor 的 Agent 可以直接搜尋個人記憶。

### 1. 確認路徑

```bash
which python  # 在 personal-memory 環境下
# → /Users/yourname/.virtualenvs/personal-memory/bin/python
```

### 2. 設定 Claude Desktop

編輯 `~/.claude/claude_desktop_config.json`：

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

重啟 Claude Desktop 後，Agent 即可使用以下工具：

| 工具 | 說明 |
|------|------|
| `search_memory(query, top_k)` | 語義搜尋所有記憶（中文支援） |
| `get_profile(section)` | 讀取個人 Profile（bio/career/family_pets/preferences） |
| `list_journals(year, limit)` | 列出手札清單 |
| `read_file(path)` | 讀取任意 DB 內的檔案 |

### 3. 設定 Cursor（MCP Workspace）

在專案根目錄新增 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "personal-brain": {
      "command": "/Users/yourname/.virtualenvs/personal-memory/bin/python",
      "args": ["00_System/mcp_server.py"]
    }
  }
}
```

---

## 依賴套件

```bash
workon personal-memory
pip install -r 00_System/requirements.txt
```

```
python-snappy>=0.7    # .pages 檔案解壓縮
watchdog>=6.0         # 檔案監控
chromadb>=0.5         # 向量資料庫
mcp>=1.0              # MCP Server
sentence-transformers # 由 chromadb 自動拉取
```

---

## .gitignore 建議

```
00_System/chroma_db/
00_System/__pycache__/
.DS_Store
```
