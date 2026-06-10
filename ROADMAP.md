# Memosyne 路線圖

> 範圍：v0.2 之後的所有 release 層級規劃。
> 每個 release 有自己的 `docs/v0.X_*.md` 計畫文件；本檔做總覽。
> v0.2-era 的細項追蹤 (`TODO_retrieval_v2.md`) 已歸檔到 `docs/archive/`。
> 文檔與示例資料的隱私規範以 `AGENTS.md` 為準。

## 定位

Memosyne 是一個本地優先的個人記憶基礎設施層，供 AI Agent 透過 MCP 等介面讀取、檢索、修正與維護長期記憶。

核心價值不是單一檢索技巧，而是一個完整生命週期：

- 將資料入庫到人類可讀的 Vault
- 以 Ground-Truth Preserving 原則做語意增強
- 以 dense、sparse、graph、temporal、cognitive signals 混合檢索
- 用可回溯流程修正錯誤記憶
- 透過 MCP 對外提供 Agent 介面
- 在檢索變更前後執行評估，避免靠感覺調參

接下來三個版本不應再優先擴張檢索層，而應讓系統更穩定、更容易使用、更容易解釋。

## Release 序列

| Release | 主題 | 主要結果 | 對外狀態 |
|---|---|---|---|
| v0.2 | 檢索凍結 | 鎖定檢索架構與評估基線 | 內部使用 |
| v0.3 | 使用產品化 | 讓日常操作可預期、可診斷 | 進階使用者 / 私有 beta |
| v0.4 | 開源發布 | 準備乾淨、可安裝、隱私安全的公開版本 | 公開 |
| v0.5 | 規模化與自我評估 | deterministic 抽取、Augury Replay、graph walk 多策略、MCP HTTP、skills、CI invariants | 公開 |
| v0.6 | 累積型來源 | turn-level 去重，正確處理 Gemini 續寫等增量匯入 | 公開 |
| v0.7 | 易用性與開源體驗 | quickstart、CLI 一致性、錯誤訊息升級、文檔重組、多 provider 一鍵切換、release 工具 | 公開 |
| v0.8 | 分散式運算 | LLM 全雲端、embedding 走遠端 GPU；providers/health 涵蓋 remote endpoint（WS4–WS6 延後，見 docs/v0.8_deferred.md） | 已交付 |
| v1.0 | 主動式記憶累積 | The Call of the Muses：缺口分析 + 每日主動提問；答案走標準入庫管線；MCP agent 訪談介面 | 開發中 |

## v0.2：檢索凍結

詳細計畫：[docs/v0.2_retrieval_freeze.md](docs/v0.2_retrieval_freeze.md)

目標：凍結目前檢索架構，建立可辯護、可重現的基線。

關鍵決策：

- 除非評估證明有具體瓶頸，否則不新增檢索模組。
- 保留目前混合架構：Dense + BM25 + Tapestry/PPR + RRF + ACT-R。
- 將 Muse routing 定位為 top-5 體驗優化，並明確記錄 Recall@10 代價。
- 所有檢索變更都要有評估報告支撐。

出口標準：

- 最新 Eternal Mirror 報告已提交或明確引用。
- Augury golden set 已建立，且使用合成或脫敏問題。
- `_vault` 產物變更已決定要提交或忽略。
- 細項任務追蹤已對齊實際 repo（v0.2 era 的 TODO 見 `docs/archive/`）。

## v0.3：使用產品化

詳細計畫：[docs/v0.3_productization.md](docs/v0.3_productization.md)

目標：降低日常操作摩擦。

關鍵決策：

- 建立一致的 `memosyne` command surface，而不是讓使用者直接記多個腳本。
- 神話命名保留在品牌與輸出細節中；工程行為必須普通、穩定、可預期。
- 優先修補可靠性與診斷能力，不新增認知功能。
- 明確處理常見失敗：模型缺失、Kuzu 缺失、索引過期、Vault dirty。

出口標準：

- 可透過文檔完成 init、ingest、search、rebuild、health、MCP 啟動。
- 常見維護任務都有唯一建議路徑。
- 文檔說清楚哪些流程呼叫本地模型、雲端模型或不呼叫模型。
- runtime prerequisites 可檢查、可診斷。

## v0.4：開源發布

詳細計畫：[docs/v0.4_oss_release.md](docs/v0.4_oss_release.md)

目標：讓專案可安全公開，且不需要讀者理解任何私有資料脈絡。

關鍵決策：

- 不發布私有 vault、key、cache、個人 benchmark report 或任何可回推私人內容的資料。
- 提供合成 sample vault，完整展示結構與 demo。
- 將隱私邊界寫成一等文檔。
- 對外定位為 memory infrastructure，而不是 generic chatbot。

出口標準：

- 公開 repo 不含私有記憶資料或 secret。
- sample vault 可支援 quickstart 和 demo commands。
- clean machine 安裝路徑可執行。
- README 可在 10 分鐘內說清楚價值、架構、隱私模型與限制。

## Release 治理

每個 release 都應定義：

- `Scope`：允許變更什麼
- `Non-goals`：明確延後什麼
- `Validation`：合併前必跑指令或報告
- `Artifacts`：預期產出的文檔、報告、sample data 或 package files

檢索相關變更的預設驗證：

```bash
workon personal-memory
cd Personal_Brain_DB/00_System
make eval-ci
```

操作與健康檢查相關變更的最低驗證：

```bash
workon personal-memory
python Personal_Brain_DB/00_System/mneme_weight.py --stats
python Personal_Brain_DB/00_System/tapestry.py --stats
python Personal_Brain_DB/00_System/slumber.py --stats
```

## v1.0：主動式記憶累積（The Call of the Muses）

詳細文檔：[docs/call_of_muses.md](docs/call_of_muses.md)

目標：讓 Memosyne 從被動歸檔變成主動累積——系統知道自己缺什麼，並開口問。

關鍵決策：

- 缺口分析必須 deterministic（不依賴 LLM、不依賴向量索引），clean clone 即可用。
- 五種缺口來源：空白領域、Profile 主題缺漏、單薄人物、日記空白月份、領域停滯。
- 回答不開新管線：寫入 spring/ 走標準 ingest → enrich → vectorize。
- 冷卻 ledger 防止重複提問；answered 是終態。
- Agent 介面走 MCP（muse_call / muse_answer），與 CLI 共用同一套選題邏輯。

出口標準：

- `memosyne call` 互動儀式可用；`--list --json` 可供 cron / agent 消費。
- 空 vault、無 kuzu、無 LLM 的環境下功能完整降級（不報錯）。
- 單元測試涵蓋缺口偵測、冷卻、選題決定性、回答落地。

## 目前建議優先順序

v1.0 開發中（The Call of the Muses）— 主動式記憶缺口提問。

v1.0 之後的候選方向：

1. **WS6 partial enrichment（v0.6 舊債）**：turn-aware 路徑只 enrich 新 turns（見 docs/v0.8_deferred.md）。

1. **Aletheia turn-level correction**：v0.6 後記憶身分到 turn 層，correction 也該下沉。
2. **OAuth 2.1 for MCP HTTP**：等真實有人從外部連 HTTP 後再做。
3. **PyPI 套件化**：`pip install memosyne` 而不是 `pip install -e .`。
4. **更多累積型 source parser**（Slack / Discord / WhatsApp）：架構已支援，等真實 fixture。

每個方向值得獨立 planning doc 再開分支；不要在 master 直接動手。
