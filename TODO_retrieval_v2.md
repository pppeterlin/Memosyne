# TODO — Retrieval v2（v0.2 分支）

> 持久化追蹤檔案。每次 commit 後請更新此檔狀態。
> 詳細設計與依據：[優化方案_索引與保存管理.md](優化方案_索引與保存管理.md)
> 分支：`v0.2`（將釋出為 v0.2.0；master 保留為 v0.1.0）
> 最後更新：2026-04-23（Phase 5.1/5.2/5.4 Eternal Mirror 落地；Phase 6.1/6.2 Aletheia skeleton + MCP）

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

#### 4.1.a Aggregation Dream — 跨記憶聚合 → **已實作（commit 待填）**
- [x] `slumber.py --aggregate` 儀式
- [x] `_collect_all_personal_facts()` 遍歷全庫（容錯 flow / block YAML）
- [x] LLM 依 7 個固定主題 slug 聚合（places / people / possessions /
  habits / values / career / interests）
- [x] 產出寫入 `10_Profile/aggregates/<slug>.md`（與 reflections 區分）
- [x] frontmatter 附 `source_paths[]`；文末列事實原文可追溯
- [x] 僅 `--aggregate` 或 `--all` 才觸發（LLM-heavy，不併入預設儀式）
- [ ] 增量更新（目前每次全量重生）—— 延後
- [ ] **[使用者任務]** backfill personal_facts 後再跑 `--aggregate`

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

### 4.3 LLM 查詢分解 → **Phase 4.7 已實作（commit 599b7e7）**
- [x] 複雜 query → LLM 拆成 (時間, 人, 地點, 動作) 子 query
- [x] 每個子 query 各走 RRF，最後再 RRF 合併
- [x] `query_decompose.py`：is_complex 啟發式 + LLM decompose + graceful fallback
- [x] `vectorize.search(decompose=True, decompose_model=...)` 整合
- [ ] **[使用者任務]** 待 LLM proxy 可用時實測對比

### 4.4 AI 對話分流（解類型 B 問題）

> 客觀事實型 AI 對話（如「海信螢幕排列方式」）和個人興趣弱相關，
> 在個人記憶檢索中應降權，避免稀釋。

- [x] `enrich.py` Oracle 新增 `chat_category` 欄位與 prompt 指令（僅對 `20_AI_Chats/` 啟用）
- [x] `validate_entities` 做白名單驗證（personal/knowledge/mixed/空）
- [x] `rewrite_file_with_enrichment` 寫入 frontmatter
- [x] `vectorize.py`：meta_base 帶入 `chat_category`，三路搜尋結果 dict 保留欄位
- [x] `search()`：對 `chat_category="knowledge"` 無條件軟降權 ×0.85
- [x] `backfill_chat_category.py`：輕量級 backfill script（只跑一輪分類、不重算 enrichment）
- [ ] **[使用者任務]** 跑 backfill：`python3 backfill_chat_category.py --apply`（76 檔，約 2–3 分鐘）
- [ ] **[使用者任務]** Backfill 完後 `vectorize.py --rebuild` 讓 meta 進索引
- [ ] **[使用者任務]** Eternal Mirror 對比前後差異
- [ ] Tapestry 邊權重：`chat_category=knowledge` 邊降權（後續選做）

---

## Phase 5 — 固化評估框架（The Eternal Mirror）

> 目的：建立**零人工標註**的可持續評估流程，讓每次優化前後都能自動對比檢索品質。
> 與 Phase 1.1 Augury（人工 golden_set）並列為兩大評估支柱：
> - **Augury**：人工挑題，衡量真實使用體驗（品質偏主觀）
> - **Eternal Mirror**：自監督，衡量檢索機械能力（數字客觀穩定）

### 5.1 HyQE Round-trip 自監督評估 → **已實作**
- [x] 建立 `Personal_Brain_DB/00_System/benchmark/retrieval_eval.py`
  - [x] 從 `hyqe_cache.json` 隨機抽 N 題（預設 500）
  - [x] 每題 query 綁定 source chunk（path + para_idx）
  - [x] 呼叫 `search()` 跑 top-K
  - [x] 指標：Recall@1 / Recall@5 / Recall@10 / MRR
  - [x] 分層報告：依繆思領域
- [x] 支援多組設定 A/B：`--config baseline` vs `--config full`
- [x] 報告輸出至 `benchmark/reports/eval_YYYYMMDD_HHMM.json` + markdown
- [x] 自動 diff 上一份報告（上升/下降標註）
- [x] `--sample-seed` 固定隨機種子，確保可重現

### 5.2 CI/Workflow 整合 → **已實作（commit 6f4dd28）**
- [x] `Makefile`：`make eval` / `eval-full` / `eval-hygiene` / `eval-compare` / `eval-ci`
- [x] 設定回歸閾值：REGRESSION_THRESHOLDS（2%），`--fail-on-regression` 退出碼 1
- [ ] **[使用者任務]** 每次優化 commit 前後各跑一次，報告附在 commit message

### 5.3 與 Augury 互補
- [ ] `retrieval_eval.py` 支援讀 `golden_set.yaml` 做混合評估
- [ ] 統一報告格式：Eternal Mirror（自監督）+ Augury（人工）雙欄對照
- [ ] Slumber `--stats` 加入最近一次評估摘要

### 5.4 評估數據的衛生 → **已實作（commit 6f4dd28）**
- [x] 避免資料洩漏：`--hygiene` 旗標將 `view=hyqe` 從 dense 檢索結果排除
  - 實作：`search(exclude_views=["hyqe"])` 傳入 Chroma `$ne` 過濾
  - 實測洩漏幅度僅約 2%，框架可信
- [ ] 抽樣策略：依 chunk 長度/繆思分層抽樣（deferred to Phase 5.5）

---

## Phase 6 — Aletheia（對話式記憶更正）

> 神話定位：**Aletheia（Ἀλήθεια）— 真理/揭露女神，Lethe 的反面**。
> Lethe 令記憶沉沒，Aletheia 使記憶顯真。
> 與 Ordeal（批次自動仲裁）互補：Aletheia 是**使用者對話式手動介入**的 curation 管道。
>
> 目標：不需手動編輯文字檔，透過和 agent 對話就能修復/更正記憶庫。

### 6.1 Aletheia 核心引擎 → **已實作（commit cd26f9d）**
- [x] 建立 `Personal_Brain_DB/00_System/aletheia.py`（CLI skeleton）
- [x] 操作類型（精簡為五項）：
  - [x] `ADD_FACT` — 追加 personal_facts
  - [x] `UPDATE_FACT` — substring-match 替換 fact
  - [x] `INVALIDATE_FACT` — substring-match 移除 fact（log 保留原文）
  - [x] `CORRECT_TEXT` — 本文字面 substring 替換（old 必須唯一）
  - [x] `REVERT` — 讀 log entry 反向操作
  - [ ] `MERGE` — 延後至 Naming Rite 整合
- [x] 所有變更寫 `aletheia_log.jsonl`（reversible，含 before/after）
- [x] 支援 `--revert <log_id>` 還原任一操作
- [x] Dry-run 預設；`--apply` 才寫入；unified diff 預覽
- [x] 歧義 substring 拒絕執行，要求更長的唯一子串

### 6.2 MCP 對話接口 → **已實作（commit ed5961c）**
- [x] 新增 5 個 MCP tools：`aletheia_add_fact` / `aletheia_update_fact` / `aletheia_invalidate_fact` / `aletheia_correct_text` / `aletheia_revert`
- [x] 預設 `apply=False`（dry-run），回傳 `_aletheia_summarize(entry)` 文字

### 6.3 Tapestry 整合 → **已實作（commit 876b5e9）**
- [x] Aletheia 改 personal_facts → 自動同步 Tapestry 邊
  - ADD/CORRECT/UPDATE → re-weave（MERGE 冪等）
  - INVALIDATE → `invalidate_edge()` 按 evidence 前綴比對 person_loc 邊
  - `invalidated_by` 標記 `aletheia:<log_id>`，可追溯
- [x] `--no-sync` 旗標跳過同步（bulk / Kuzu lock 時用）
- [ ] MERGE 操作觸發 Naming Rite 的 alias 合併邏輯（延後）

### 6.4 安全網 → **已實作（commit 待填）**
- [x] 每次 apply 前自動 shadow copy 到 `aletheia_backup/<timestamp>_<log_id>/`
- [x] 高風險 CORRECT_TEXT 需 `--confirm`（長字串 / 跨行 / 大幅長度差觸發）
- [x] body 改動標記到 `aletheia_pending_reembed.json`，使用者跑 `vectorize.py --rebuild` 即可重嵌
- [x] `.gitignore`：log + backup + pending 不進 git
- [ ] 單檔 re-embed 介面（目前仍需 full rebuild）—— 延後

---

## Phase 7 — The Invocation Protocol（Agent ↔ Memory 系統化介接）

> **動機**：目前 agent 如何呼叫 memory DB，是靠使用者在 chat 裡即興說「去查一下 X」或
> 臨時改 prompt。這種 ad-hoc 模式無法規模化，每次遇到新情境就得重寫 prompt。
> 應該把「何時搜、搜什麼、怎麼融合結果、何時寫、何時更正」做成**系統化協議**，
> 以 Skill + MCP tool 的組合正規化。
>
> 神話定位：**The Invocation**（召喚儀式）——不是亂叫女神，是依循儀軌呼喚正確的 Muse。

### 7.1 行為分類與觸發條件 → **已實作（commit 待填）**
- [x] 盤點 6 個 invocation classes：READ-RECALL / READ-CONTEXT /
  READ-TEMPORAL / WRITE-INGEST / WRITE-CORRECT / WRITE-ANNOTATE
- [x] 每一類給出觸發啟發式（zh + en 關鍵詞清單）

### 7.2 Skill 設計（`memosyne-invocation`）→ **已實作（commit 待填）**
- [x] 建立 `Personal_Brain_DB/00_System/skills/memosyne-invocation/SKILL.md`
- [x] 完整決策樹：query → 分類 → 對應 MCP tool
- [x] 「應呼叫」vs「不該呼叫」對照範例 + 反模式列舉
- [x] Query hygiene（剝離禮貌 / 元框架 / 保留專有名詞）
- [x] 後處理守則（interpret 而非 dump、引用格式、空結果誠實）
- [x] 安全守則（apply=False 預設、snapshot、revert log_id）
- [ ] **[使用者任務]** 若需全域啟用：symlink 到 `~/.cursor/skills-cursor/`
  或 `~/.claude/skills/`

### 7.3 MCP 介面整理 → **已實作（commit 待填）**
- [x] 新增 meta-tool `memosyne_guide(situation)`：輸入自然語言情境，
  回傳建議 invocation class + tool + 範例 + 注意事項
- [x] 現有 tools 在 SKILL.md Appendix A 完整盤點
- [ ] 介面一致化（統一回傳 schema / dry-run / 錯誤格式）—— 延後
- [ ] 文件自動產生 —— 延後

### 7.4 評估
- [ ] 建立「agent-in-the-loop」測試集：一組真實對話，檢驗 agent 是否在正確時機呼叫正確 tool
- [ ] 指標：召回命中率、誤觸發率、使用者干預次數
- [ ] Skill 調整前後對比

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
