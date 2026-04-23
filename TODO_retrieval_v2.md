# TODO — Retrieval v2（v0.2 分支）

> 持久化追蹤檔案。每次 commit 後請更新此檔狀態。
> 詳細設計與依據：[優化方案_索引與保存管理.md](優化方案_索引與保存管理.md)
> 分支：`v0.2`（將釋出為 v0.2.0；master 保留為 v0.1.0）
> 最後更新：2026-04-23（Muse Router adaptive boost 成為預設；Phase 5 Eternal Mirror 落地）

## 狀態圖例
- [ ] pending
- [-] in_progress
- [x] completed
- [!] blocked（請在下方註記原因）

---

## Phase 0 — 分支與文件（地基）

- [x] 建立 `feat/retrieval-v2` 分支
- [x] 產出優化方案文件 `優化方案_索引與保存管理.md`
- [x] 更新 `README.md` / `README.zh.md`：加入 v2 roadmap 與 Acknowledgements & References 區塊
- [x] 建立本 TODO 追蹤檔

---

## Phase 1 — 地基（先做，否則後面無法驗證）

### 1.1 The Augury Benchmark（**最優先**）
- [x] 建立 `Personal_Brain_DB/00_System/benchmark/` 目錄
- [x] 實作 `augury_benchmark.py`（Recall@K / MRR / P@5 / Full Recall / Abstention / 自動 diff 上一份報告）
- [x] `golden_set.yaml` 模板（含各繆思範例題）
- [x] `benchmark/README.md` 使用說明
- [ ] **[使用者任務]** 用真實記憶填寫 `golden_set.yaml`，每位繆思 5–10 題
- [ ] **[使用者任務]** 跑第一次 baseline：`python3 augury_benchmark.py`
- [ ] **[使用者任務]** commit baseline 報告作為對照基準

### 1.2 The Naming Rite（實體正規化）
- [x] 在 `slumber.py` 新增 `--naming` 儀式
- [x] 收集所有 `person` 節點 → 名稱正規化（lowercase / 去連字號 / 去空白）
- [x] 對正規化字串做 embedding 相似度聚類（threshold 0.85）
- [x] LLM 二次確認候選合併對
- [x] 合併節點：邊重導 + `aliases[]` + `merged_from[]` + `merged_at`
- [x] 寫入 reversible merge log（`naming_log.jsonl`）
- [x] enrich.py 新入庫時即時 alias 偵測（查既有 aliases 表）

### 1.3 Parent-Child 切片（Small-to-Big）
- [x] 修改 `ingest.py` chunker：每個 chunk metadata 加 `parent_section_id` / `parent_doc_id` / `sibling_order`
- [x] 回填既有 chunks（migration script）
- [x] 修改 `vectorize.search()`：命中 chunk 後可選擇回傳 parent section（新增 `return_parent=True` 參數）
- [x] 更新 MCP server `query_memory` 暴露此參數
- [ ] **[使用者任務]** 跑 Augury benchmark 對比

---

## Phase 2 — 檢索深化

### 2.1 The Triple Echo（HyQE 多視角）
- [x] 在 `enrich.py` 新增 HyQE 產生步驟：每 chunk 產 3–5 個假設問題
- [x] YAML schema 加入 `hyqe_questions: []`
- [x] 修改 `vectorize.py`：嵌入三種視角（raw / summary / hyqe），metadata 加 `view ∈ {raw, summary, hyqe}`
- [x] `search()`：對同一 chunk_id 取最高分視角作為代表
- [x] 對既有記憶庫批次回填（276 files / ~11,827 questions，via `proxy:claude-opus-4-6`；長文分批 30 段）
- [x] 重建向量索引（ChromaDB 8,033 chunks，含 raw/summary/hyqe 三視角；BM25 3,858 raw）
- [ ] **[使用者任務]** Augury 對比

### 2.2 The Invocation（繆思路由器）
- [x] 建立 `muses.py`：query → 1–3 位繆思的分類器（prototype centroid + cosine）
- [x] 訓練/準備 9 繆思的 prototype embedding（用各繆思領域現有記憶做 centroid；seed < 3 的繆思略過）
- [x] 兩種模式：硬篩選（`muse_mode="hard"`）/ 軟加權（`muse_mode="soft"`）
- [x] MCP `search_memory` 接受 `muses=[]` / `auto_route=true` / `muse_mode`
- [x] **Confidence-scaled boost（adaptive，v0.2 預設）**：`boost = 1 + (router_score - threshold) × k`，clamp 至 `muse_boost_max`
  - Eternal Mirror N=500 對比（2026-04-23）：
    | 指標 | Baseline | Flat ×1.30 | **Adaptive (soft)** | Penalty | Boost top-3 |
    |---|---:|---:|---:|---:|---:|
    | R@1 | 0.036 | 0.104 | **0.108** | 0.102 | 0.100 |
    | R@5 | 0.102 | 0.148 | **0.150** | 0.142 | 0.140 |
    | R@10 | **0.248** | 0.172 | 0.174 | 0.164 | 0.166 |
    | MRR | 0.075 | 0.125 | **0.128** | 0.121 | 0.119 |
  - 預設：`muse_mode="soft", auto_route_threshold=0.20, muse_boost_k=2.0, muse_boost_max=1.5, route_top_k=2`
  - **R@10 結構性代價**：任何繆思加權都會把 graph/PPR 找到的長尾結果壓出 top-10（baseline 0.248 vs soft 0.174）。
    試過 `muse_mode="penalty"`（非命中扣分）與 `route_top_k=3`（router 覆蓋加寬），皆未救回 R@10。
    Trade-off 結論：accept R@10 代價以換 R@1/R@5/MRR，因為個人搜尋情境 top-5 優先。
  - Alternative `muse_mode="penalty"` 保留供需要時啟用（`muse_penalty_k=0.5, muse_penalty_min=0.85`）。

### 2.3 HippoRAG 2（短語+段落共圖）
- [x] 修改 `tapestry.py`：PPR seed 同時接受 phrase node（entity）與 passage node（memory）
- [x] 擴散後回傳僅取 passage node 分數作為 memory 貢獻
- [x] 保持向後相容（舊 PPR 調用為 legacy path）
- [ ] **[使用者任務]** Augury 對比（特別注意多跳題）

### 2.4 時間擴展（regex first）
- [x] 實作 `temporal_parser.py`：regex 抽取「2023 年」「去年夏天」「上個月」→ 具體日期範圍
- [x] `search()` 加入 date 過濾/加權
- [x] 時間距離 bonus（命中記憶日期越接近查詢錨點 → 加分）
- [ ] **[使用者任務]** Augury temporal 類題對比

---

## Phase 3 — 時間與真實性（結構性升級）

### 3.1 Bi-temporal Tapestry（The Two Rivers）
- [x] Tapestry schema 升級：每條 REL 邊新增 `t_valid_start` / `t_valid_end` / `t_ingested` / `invalidated_by`
- [x] Migration script：`backfill_temporal()` 回填既有邊（3374 邊）
- [x] 查詢介面：`currently_valid_edges()` / `edges_as_of(ts)` / `get_entity_timeline(entity)` / `invalidate_edge()`
- [x] `weave_memory()` 使用 `ON CREATE SET` 寫入時間戳
- [x] MCP 新增 `query_memory_at_time(query, timestamp)` 與 `get_entity_timeline(entity)`

### 3.2 The Ordeal（衝突處理 CRUD）
- [x] 在 Slumber 新增 `the_ordeal()`（`--ordeal` 旗標）
- [x] LLM 輸出 operation ∈ {ADD, UPDATE, INVALIDATE, NOOP}（MERGE 由 Naming Rite 處理）
- [x] 對應到 Tapestry 邊操作（UPDATE/INVALIDATE → `invalidate_edge()` 標記 `t_valid_end`）
- [x] Operation log（`ordeal_log.jsonl`），reversible
- [x] 對 personal_facts 優先啟用（按 person 分組，≥2 mentions 觸發）
- [ ] **[使用者任務]** 累積 personal_facts 後實測（目前 vault 無數據）

### 3.3 The Mirror of Truth（Self-RAG 批判）
- [x] `enrich.py --critique` 開關，產出後多一輪 LLM self-critique
- [x] 逐項檢查 personal_facts / themes / period 的語意支持度
- [x] unsupported → 剔除；partial → 保留並加入 `needs_review: []`
- [x] `--critique-min-importance` 控制只對 high/medium 批判（預設 high，降低成本）

---

## Phase 4 — 高階（選做）

### 4.1 The Dreaming（全局記憶鞏固 / REM 循環）

> **設計定位修正（2026-04-23）**：原本的 A-MEM Resonance 是「新記憶觸發舊記憶重 enrichment」，
> 屬**增量式**；但使用者願景是「像做夢一樣**全局整理**」。
> 目前 Slumber 的 Reflection 只做近 14 天時間窗口，不動舊記憶，不做聚合——缺的就是這塊。

**四個子儀式：**

#### 4.1.a Aggregation Dream — 跨記憶聚合（解類型 A 問題）
- [ ] 週期性遍歷全庫，LLM 從散落事實提煉聚合記憶
  - 例：「去過哪些國家/城市」「人際圈」「價值觀演變」→ 獨立 profile memory
- [ ] 產出寫入 `10_Profile/aggregates/`（與 reflections 區分）
- [ ] 附 `source_paths[]` 可追溯來源
- [ ] 隨新記憶增量更新而非每次重生

#### 4.1.b Repair Dream — 記憶修復
- [ ] 全庫掃描偵測矛盾 personal_facts / entities
- [ ] 呼叫 Ordeal 仲裁，必要時觸發 Aletheia 請求人工確認
- [ ] 錯誤標記 `t_valid_end` 而非刪除

#### 4.1.c Resonance Dream — 共振重 enrichment（原 A-MEM）
- [ ] 新記憶入庫後，與其重疊 ≥ 2 實體的舊記憶列入候選
- [ ] 批次重跑 enrichment，舊版本存 `enrichment_history[]`
- [ ] 頻率控制：Slumber 排程期才執行

#### 4.1.d Consolidation Dream — 結構鞏固
- [ ] 低密度散布事實 → 高密度結構節點
- [ ] Tapestry 新增 `episode` 節點（Phase 4.2），由 Dreaming 自動產生
- [ ] 高頻共現實體對 → 升級為結構化關聯（不僅 co_recalled）

### 4.2 Episode 節點
- [ ] Tapestry 新增 `episode` 節點類型
- [ ] ingest.py 切片後產 episode 節點，chunks → episode 用 `composed_of` 邊連結
- [ ] PPR 擴散經過 episode → 同 episode 的 chunks 都被激發

### 4.3 LLM 查詢分解
- [ ] 複雜 query → LLM 拆成 (時間, 人, 地點, 動作) 子 query
- [ ] 每個子 query 各走 RRF，最後再 RRF 合併
- [ ] Augury 對比：多跳題的完整率

### 4.4 AI 對話分流（解類型 B 問題）

> 客觀事實型 AI 對話（如「海信螢幕排列方式」）和個人興趣弱相關，
> 在個人記憶檢索中應降權，避免稀釋。

- [ ] ingest.py 對 `20_AI_Chats` 新增 `chat_category` enrichment 欄位：
  - `personal` — 和使用者個人生活/情緒/決策相關
  - `knowledge` — 純技術/客觀知識問答
  - `mixed` — 兼具
- [ ] LLM 分類：Oracle 入庫時判斷 category（可在 `enrich.py` 加一輪）
- [ ] Tapestry：對 `chat_category=knowledge` 的記憶邊權重降低
- [ ] search()：對純 knowledge 類 AI 對話施加軟降權（除非 query 明顯是技術問題）
- [ ] 回填既有 AI 對話記憶

---

## Phase 5 — 固化評估框架（The Eternal Mirror）

> 目的：建立**零人工標註**的可持續評估流程，讓每次優化前後都能自動對比檢索品質。
> 與 Phase 1.1 Augury（人工 golden_set）並列為兩大評估支柱：
> - **Augury**：人工挑題，衡量真實使用體驗（品質偏主觀）
> - **Eternal Mirror**：自監督，衡量檢索機械能力（數字客觀穩定）

### 5.1 HyQE Round-trip 自監督評估
- [ ] 建立 `Personal_Brain_DB/00_System/benchmark/retrieval_eval.py`
  - 從 `hyqe_cache.json` 隨機抽 N 題（預設 500）
  - 每題 query 綁定 source chunk（path + para_idx）
  - 呼叫 `search()` 跑 top-K
  - 指標：Recall@1 / Recall@5 / Recall@10 / MRR
  - 分層報告：依繆思領域 / 依 chunk 長度 / 依問題類型
- [ ] 支援多組設定 A/B：`--config baseline` vs `--config full`（讀取 YAML profile）
- [ ] 報告輸出至 `benchmark/reports/eval_YYYYMMDD_HHMM.json` + markdown
- [ ] 自動 diff 上一份報告（上升/下降標註、顯著性提示）
- [ ] `--sample-seed` 固定隨機種子，確保可重現

### 5.2 CI/Workflow 整合
- [ ] `Makefile` 或 shell script：`make eval` 一鍵跑 baseline + full
- [ ] 每次優化 commit 前後各跑一次，報告附在 commit message
- [ ] 設定回歸閾值：Recall@5 下降 > 2% 視為回歸，需手動確認

### 5.3 與 Augury 互補
- [ ] `retrieval_eval.py` 支援讀 `golden_set.yaml` 做混合評估
- [ ] 統一報告格式：Eternal Mirror（自監督）+ Augury（人工）雙欄對照
- [ ] Slumber `--stats` 加入最近一次評估摘要

### 5.4 評估數據的衛生
- [ ] 避免資料洩漏：HyQE 問題已嵌入索引，需剔除「query 命中自己的 hyqe view」這種偽命中
  - 做法：搜尋結果 metadata 若 view=hyqe 且 source 與 query 同 chunk，視為退化命中（可選懲罰或忽略）
- [ ] 抽樣策略：依 chunk 長度/繆思分層抽樣，避免長文或熱門繆思壟斷

---

## Phase 6 — Aletheia（對話式記憶更正）

> 神話定位：**Aletheia（Ἀλήθεια）— 真理/揭露女神，Lethe 的反面**。
> Lethe 令記憶沉沒，Aletheia 使記憶顯真。
> 與 Ordeal（批次自動仲裁）互補：Aletheia 是**使用者對話式手動介入**的 curation 管道。
>
> 目標：不需手動編輯文字檔，透過和 agent 對話就能修復/更正記憶庫。

### 6.1 Aletheia 核心引擎
- [ ] 建立 `Personal_Brain_DB/00_System/aletheia.py`
- [ ] 操作類型（沿用並擴展 Ordeal 語意）：
  - `UPDATE` — 改 YAML 欄位（personal_facts / themes / period / importance）
  - `INVALIDATE` — 標記錯誤記憶，設 `t_valid_end`
  - `MERGE` — 合併重複實體（人名別名等，呼叫 Naming Rite）
  - `ANNOTATE` — 加備註但不改原始文本
  - `CORRECT_TEXT` — 改正原文錯字/事實錯誤（謹慎使用）
- [ ] 所有變更寫 `aletheia_log.jsonl`（reversible，含 before/after diff）
- [ ] 支援 `--revert <log_id>` 還原任一操作

### 6.2 MCP 對話接口
- [ ] 新增 MCP tool `aletheia_correct(memory_path, instruction, dry_run=True)`
- [ ] 新增 MCP tool `aletheia_revert(log_id)`
- [ ] agent 流程：讀記憶 → 呈現現狀 → 用戶口述修正 → dry-run 顯示 diff → 確認後 apply
- [ ] 預設 dry_run=True，避免誤改

### 6.3 Tapestry 整合
- [ ] Aletheia 改 personal_facts → 自動同步 Tapestry 邊（新增/invalidate）
- [ ] MERGE 操作觸發 Naming Rite 的 alias 合併邏輯

### 6.4 安全網
- [ ] 每次 apply 前自動 git snapshot（或 shadow copy 到 `aletheia_backup/`）
- [ ] 高風險操作（CORRECT_TEXT / MERGE）需二次確認
- [ ] Aletheia 操作觸發該記憶 re-embedding（保持索引一致）

---

## 不做（避免過度工程）
- ❌ ColBERT 晚期互動（除非 Augury 證明 dense 是瓶頸）
- ❌ MemGPT-style 分頁（Claude context window 夠）
- ❌ 完整 GraphRAG 社群重建（成本高，LightRAG 增量夠用）
- ❌ 即時 A-MEM 重算（只在 Slumber 做）
- ❌ 命題化切片（與 HyQE 重疊度高，優先 HyQE）

---

## 每次 commit 時請做
1. 更新本檔對應 task 的狀態
2. 若新增或取消 task，同步更新
3. 若有 blocked，在該 task 下用 `> blocker: ...` 註記

## 會話恢復指引
新的 Claude Code session 開始時，先看：
1. 本檔（目前進度）
2. `優化方案_索引與保存管理.md`（設計依據）
3. `CLAUDE.md`（神話主題命名與專案規範）
4. 最新一次的 `reports/` 內 Augury benchmark 結果（當前 baseline）
