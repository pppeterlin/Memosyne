# Privacy Model

Memosyne 的設計假設前提：**記憶屬於使用者，預設不離開本機**。

本文件回答開源使用者最常問的隱私問題：哪些資料只留在本地、哪些命令會呼叫雲端、哪些檔案是私有衍生物、如何刪除或重建。若行為與此文件描述不一致，視為 bug。

---

## 一、資料分層

| 層 | 路徑 | 性質 | 是否進公開 repo |
|---|---|---|---|
| Source code | `memosyne.py`、`Personal_Brain_DB/00_System/`、`docs/` | 公開 | ✅ |
| Sample vault | `sample_vault/` | 完全合成 | ✅ |
| Private vault | `Personal_Brain_DB/_vault/` | 使用者私有記憶 | ❌（gitignored / submodule） |
| Derived artifacts | 索引、快取、access log、graph DB | 由 vault 衍生 | ❌ |
| Secrets | API keys、`.env`、`openrouter-key` | 認證 | ❌ |

「Derived artifacts」指任何由 vault 內容生成、能反推私人資訊的檔案：

```
Personal_Brain_DB/00_System/chroma_db/          # Dense embeddings
Personal_Brain_DB/_vault/bm25_index.pkl         # BM25 索引
Personal_Brain_DB/_vault/chronicle.db           # ACT-R 存取紀錄
Personal_Brain_DB/_vault/chronicle.jsonl        # 存取紀錄原始流
Personal_Brain_DB/_vault/tapestry_db/           # Kuzu graph
Personal_Brain_DB/_vault/contextual_cache.json  # Contextual Retrieval 快取
Personal_Brain_DB/_vault/hyqe_cache.json        # HyQE 快取
Personal_Brain_DB/_vault/muse_centroids.json    # Muse 路由 centroid
Personal_Brain_DB/_vault/augury_reports/        # Augury 評估報告
Personal_Brain_DB/_vault/benchmark_reports/     # Eternal Mirror 評估
Personal_Brain_DB/00_System/aletheia_log.jsonl  # Correction 紀錄
Personal_Brain_DB/00_System/benchmark/golden_set.yaml  # 含真實查詢
```

`.gitignore` 已涵蓋以上路徑。私有 vault 透過 git submodule 連到使用者自己的 private repo。

---

## 二、Local vs Cloud — 命令分類

預設行為一律本地。Cloud 呼叫需要使用者明確指定 cloud provider 與對應憑證，沒設定就不會發出。

### 完全本地（不需任何 LLM）

| 命令 | 說明 |
|---|---|
| `memosyne init` | 建立目錄 |
| `memosyne health` | 檢查環境與 artifacts |
| `memosyne search "query"` | Dense + BM25 + Tapestry 三路檢索 + Chronicle rerank |
| `memosyne chronicle --stats` | ACT-R 存取統計 |
| `memosyne tapestry --stats` | 圖譜統計 |

> **說明**：search 用本地 sentence-transformers embedding 模型（已快取則 offline）。`MEMOSYNE_HF_OFFLINE=1` 為預設。

### 本地 LLM（需自備本地 LLM 後端）

| 命令 | 用途 |
|---|---|
| `memosyne ingest` | Oracle 抽取 entities / themes |
| `memosyne rebuild --contextualize` | Contextual Retrieval 摘要 |
| `memosyne slumber --reflect` | 記憶反射 / 整合 |

> **說明**：以上命令需要一個本地可呼叫的 LLM endpoint。Memosyne 不綁定特定 runtime——使用者可以選擇 Ollama、llama.cpp、LM Studio、vLLM、或任何提供相容 API 的本地服務。
>
> 模型本身、模型大小、是否量化、是否 offline，皆由使用者依硬體與需求決定。請依各 runtime 的文件設定 host、port 與模型名稱，並在 Memosyne 的設定（`.env` 或 `memosyne.toml`）中填入對應的 endpoint 與 model identifier。
>
> 從 Memosyne 角度，唯一的隱私保證是：**這些命令只會把資料送到使用者指定的 endpoint**。endpoint 是不是真的在本機，由使用者自己保證。

### 可選 Cloud LLM（須 opt-in）

只要使用者在設定中明確指向 cloud provider 並提供憑證，相關命令才會走雲端。沒設定就不會發出。

常見 opt-in 形式：

- 設定 `LLM_PROVIDER` 或 model identifier 指向 cloud provider（例如 OpenRouter、自架 OpenAI-compatible proxy）。
- 提供對應的 API key 環境變數或 key 檔。

實際支援的 provider 視 `Personal_Brain_DB/00_System/llm_client.py` 為準；新增 provider 時應同步更新本文件。

### 永遠不離開本機

- 你的 vault 內容、查詢字串、access log、評估報告、correction 操作紀錄。
- 即便使用 cloud LLM，傳出的也只是「當下被處理的那段文字」（如 enrichment 的單一 chunk），不會主動上傳整個 vault。

---

## 三、MCP Server

`memosyne mcp` 啟動本地 MCP server（stdio transport）。當你把它接到 Claude Desktop 或 Cursor 時：

- MCP client（Claude Desktop / Cursor）會把 **你問的問題** 傳給 Anthropic / 對應的 LLM 供應商。
- Memosyne 回傳的 **檢索結果** 會作為 context 一併送出。
- 也就是說：**接上 cloud LLM client 之後，你的記憶片段會以 context 形式離開本機**。這是 LLM client 的固有行為，不是 Memosyne 主動上傳。

如果你不想讓記憶片段被任何雲端 LLM 看到，請：
- 用本地 LLM 客戶端（如 Ollama-based 工具）作為 MCP host，或
- 不啟用 MCP，僅使用 `memosyne search` 在 terminal 操作。

詳見 [mcp.md](mcp.md)。

---

## 四、Correction 與 Audit Trail

`memosyne correct` 提供 dry-run 模式：所有修改先輸出 diff，使用者確認後才落盤。

- 修改紀錄寫到 `aletheia_log.jsonl`（gitignored），可審計、可回滾。
- 重新 embed 的 chunk 進入 `aletheia_pending_reembed.json` 排程。
- 沒有任何修正操作會主動上傳到雲端。

詳見 [correction.md](correction.md)。

---

## 五、刪除與重建

衍生 artifacts 都可以重建。私有來源檔案是唯一不可重建的資料。

| 想做什麼 | 命令 |
|---|---|
| 重建所有索引 | `python memosyne.py rebuild` |
| 清空存取紀錄 | 刪除 `chronicle.db` 與 `chronicle.jsonl` |
| 清空 LLM 快取 | 刪除 `contextual_cache.json`、`hyqe_cache.json` |
| 重建 graph | `python Personal_Brain_DB/00_System/tapestry.py --rebuild` |
| 完全離開 Memosyne | 刪除 repo 與你自己的 private vault submodule 即可，沒有 hosted 服務需要登出 |

---

## 六、貢獻者注意事項

提交 PR / issue 時：

- ❌ 不要附上你的 `chronicle.db`、`tapestry_db/`、`bm25_index.pkl`、`contextual_cache.json` 等衍生檔案（即使是除錯用）。
- ❌ 不要在 stack trace、log、或文檔範例貼出真實人名、地名、私人事件。
- ✅ 用 `sample_vault/` 重現問題；若 sample 無法重現，請改寫成不可回推的合成資料再附上。
- ✅ 提交前跑 `git diff --stat origin/master...HEAD` 檢查沒有意外帶到私有檔案。

---

## 七、不承諾

Memosyne 不提供：

- Hosted sync（沒有伺服器）
- 多使用者協作（無 access control 模型）
- E2E 加密的雲端備份
- 醫療 / 法律等級的隱私合規認證

如果你的威脅模型需要以上任何一項，Memosyne 不適合作為唯一的記憶層。
