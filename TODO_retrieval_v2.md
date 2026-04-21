# TODO — Retrieval v2（v0.2 分支）

> 持久化追蹤檔案。每次 commit 後請更新此檔狀態。
> 詳細設計與依據：[優化方案_索引與保存管理.md](優化方案_索引與保存管理.md)
> 分支：`v0.2`（將釋出為 v0.2.0；master 保留為 v0.1.0）
> 最後更新：2026-04-21

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
- [ ] 在 `enrich.py` 新增 HyQE 產生步驟：每 chunk 產 3–5 個假設問題
- [ ] YAML schema 加入 `hyqe_questions: []`
- [ ] 修改 `vectorize.py`：嵌入三種視角（raw / summary / hyqe），metadata 加 `view ∈ {raw, summary, hyqe}`
- [ ] `search()`：對同一 chunk_id 取最高分視角作為代表
- [ ] Augury 對比

### 2.2 The Invocation（繆思路由器）
- [ ] 建立 `muses.py`：query → 1–3 位繆思的分類器（embedding 分類 or 輕量 LLM）
- [ ] 訓練/準備 9 繆思的 prototype embedding（用各繆思領域現有記憶做 centroid）
- [ ] 兩種模式：硬篩選（明確領域詞）/ 軟加權（×1.3）
- [ ] MCP `query_memory` 接受 `muses=[]` 或 `auto_route=true`
- [ ] Augury 對比

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
- [ ] Tapestry schema 升級：每條邊新增 `t_valid_start` / `t_valid_end` / `t_ingested` / `invalidated_by`
- [ ] Migration script：既有邊 → `t_valid_start=t_ingested` / `t_valid_end=null`
- [ ] 查詢介面：`as_of(ts)` / `currently_valid()` / `historical_beliefs()`
- [ ] MCP 新增 `query_memory_at_time(query, timestamp)` 與 `get_entity_timeline(entity)`

### 3.2 The Ordeal（衝突處理 CRUD）
- [ ] 在 `enrich.py` 或 Slumber 新增 conflict detection
- [ ] LLM 輸出 operation ∈ {ADD, UPDATE, INVALIDATE, MERGE, NOOP}
- [ ] 對應到 Tapestry 邊操作（UPDATE → 舊邊 `t_valid_end` 設定；INVALIDATE → 標記失效不刪）
- [ ] Operation log（`ordeal_log.jsonl`），reversible
- [ ] 對 personal_facts 優先啟用，逐步擴展

### 3.3 The Mirror of Truth（Self-RAG 批判）
- [ ] `enrich.py` 產出後多一輪 LLM self-critique
- [ ] 逐項檢查 YAML 欄位是否能在原文找到字面依據
- [ ] 不支持項目 → 剔除或 `needs_review: true`
- [ ] 可批次處理降低成本
- [ ] 加入 `enrich.py --critique` 開關，先用於 high-importance 記憶

---

## Phase 4 — 高階（選做）

### 4.1 The Resonance（A-MEM 記憶演化）
- [ ] Slumber 新增「共鳴儀式」：新記憶觸發重疊 ≥ 2 實體的舊記憶重新 enrichment
- [ ] 舊 enrichment 保留至 `enrichment_history[]`，版本化
- [ ] 頻率控制：僅在 Slumber 排程期執行

### 4.2 Episode 節點
- [ ] Tapestry 新增 `episode` 節點類型
- [ ] ingest.py 切片後產 episode 節點，chunks → episode 用 `composed_of` 邊連結
- [ ] PPR 擴散經過 episode → 同 episode 的 chunks 都被激發

### 4.3 LLM 查詢分解
- [ ] 複雜 query → LLM 拆成 (時間, 人, 地點, 動作) 子 query
- [ ] 每個子 query 各走 RRF，最後再 RRF 合併
- [ ] Augury 對比：多跳題的完整率

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
