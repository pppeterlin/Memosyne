# Memosyne 路線圖

> 範圍：v0.2 檢索凍結、v0.3 使用產品化、v0.4 開源發布準備。
> 本文件負責 release 層級規劃；`TODO_retrieval_v2.md` 繼續作為細項任務追蹤。
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
- `TODO_retrieval_v2.md` 狀態與實際 repo 一致。

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

## 目前建議優先順序

立即工作應以 v0.2 收口為主：

1. 同步 `TODO_retrieval_v2.md` 與實際狀態。
2. 決定 dirty `_vault` submodule 變更要提交或忽略。
3. 建立小型 Augury golden set，並確保只使用合成或脫敏問題。
4. 記錄目前檢索基線作為 freeze point。

完成後再進入 v0.3 packaging 與 CLI 工作。
