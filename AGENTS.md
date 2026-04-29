# Memosyne — Project Rules

## 核心靈感：希臘神話記憶女神 Mnemosyne

本專案名稱 **Memosyne** = Memory + Mnemosyne（記憶女神）。

所有設計、命名、提示語都應圍繞這個神話世界觀展開。
「記憶入庫」不只是資料處理，而是一場**神聖的儀式**。

---

## 神話世界觀

```
Mnemosyne（泰坦神，記憶女神）
    ├── 她的聖泉：Spring of Mnemosyne — 飲此泉水，靈魂記憶永不遺失
    ├── 她的對立：River Lethe（忘川）  — 飲此水，遺忘一切
    └── 她的九個女兒：The Nine Muses（繆思女神）
            ├── Clio        歷史      → 30_Journal（日記）
            ├── Thalia      喜劇/生活 → 30_Journal（隨筆）
            ├── Calliope    史詩/對話 → 20_AI_Chats
            ├── Urania      天文/知識 → 50_Knowledge
            ├── Polyhymnia  神聖詩歌  → 10_Profile（身份）
            ├── Erato       愛情詩   → 感情相關記憶
            ├── Melpomene   悲劇     → 低潮/反思記憶
            ├── Terpsichore 舞蹈     → 40_Projects（行動）
            └── Euterpe     音樂     → 創作/靈感記憶

記憶庫核心角色：
    Oracle of Mneme  — 負責 Enrichment 的 LLM 角色
    The Spring       — spring/ 入口資料夾
    The Vault        — Personal_Brain_DB/ 記憶庫主體
```

---

## 命名規範（Naming Convention）

新功能或模組命名時，優先從神話中取靈感：

| 功能類型 | 建議命名方向 | 例子 |
|---------|------------|------|
| 資料入口 / drop zone | Spring, Well, Threshold | `spring/` |
| 記憶儲存 / vault | Vault, Codex, Tapestry | `Personal_Brain_DB/` |
| 搜尋 / 查詢 | Oracle, Divination, Echo | `oracle query` |
| 分析 / 洞察 | Augury, Omen, Revelation | gap analysis = "The Call of the Muses" |
| 刪除 / 歸檔 | Lethe, Oblivion | 歸檔 = "Surrendered to Lethe" |
| 定期任務 | Rite, Vigil, Offering | 每日入庫 = "Daily Offering" |
| 認知衰減 / 存取紀錄 | Chronicle, Mneme | access log = "The Chronicle of Mneme" |
| 語境化增強 | Illumination, Revelation | contextual notes = "The Illumination" |
| 錯誤 / 失敗 | Hubris, Nemesis | retry = "Defying Nemesis" |

---

## CLI / 輸出風格

程式的輸出訊息可以帶有儀式感，但**不強制**——在不影響可讀性的前提下加入。

**原則：**
- 流程開始 → 召喚感（"The Spring stirs..."）
- 完成 → 永恆感（"The tapestry grows richer."、"Nothing lost to Lethe."）
- 錯誤 → 人性化但莊重（"The Oracle faltered."）
- 空狀態 → 寧靜感（"The waters are still."）

**範例對照：**

```
❌ Processing complete. 3 files added.
✅ 🌊 3 memory fragments have found their eternal place.
   The tapestry of Memosyne grows richer.

❌ Error: LLM failed for file.md
✅ The Oracle faltered for: file.md

❌ No files to process.
✅ The Spring is still. No fragments await the Oracle.
```

---

## 文檔與示例資料隱私規範

這是 workspace-level 規範，適用於 README、roadmap、docs、sample vault、benchmark 範例、CLI 範例與公開說明。

**原則：**
- 文檔與示例可以使用通用地名、通用場景與合成資料。
- 不得複製私有記憶中的原文、原始查詢、標題、摘要或 benchmark query。
- 不得使用與私有記憶高度相同的人、地點、工作、關係、事件組合。
- 不得放入真實人名、暱稱、私人關係線索，或可回推個人經歷的描述。
- 若需要展示個人記憶能力，使用 synthetic sample vault 或明確合成的 query。

**可接受：**

```bash
memosyne search "測試查詢"
memosyne search "範例事件"
memosyne search "Tokyo 行程規劃"
```

**不可接受：**
- 從 private vault 直接取出的查詢、摘要、標題或段落
- 與 private vault 中某段記憶一模一樣或高度相似的情境
- 真實人名 / 暱稱 + 地點 + 私有事件的組合

---

## 現有儀式架構（已實作）

```
The Spring Ritual（ingest.py）
    I.   The Discernment  — 繆思女神辨識格式，路由至各自領域
    II.  The Weaving      — Oracle 提取記憶精華，編織進 YAML + 織入 Tapestry
    III. The Inscription  — 向量化，銘刻入 Vault

Oracle of Mneme（enrich.py）
    — LLM 角色，只說 JSON，不推斷、不幻想，只記錄原文出現的事實
    — 同時提取 personal_facts（個人生活事實，區別於客觀知識）
    — 每次增強後自動呼叫 tapestry.weave_memory() 更新圖

The Tapestry（tapestry.py + tapestry.json）
    — 圖拓樸記憶關聯層（networkx DiGraph）
    — 節點：memory / person / location / event / period
    — 邊：mentions / happened_at / located_in / involved_in / during
    — 解決「Tokyo → 找不到 friend-A 相關記憶」的跨實體關聯斷裂問題

The Vault（Personal_Brain_DB/）
    — 記憶的永恆居所，由繆思女神各自守護其領域

搜尋架構（vectorize.py）
    Dense（ChromaDB）+ BM25 + Tapestry Graph → 三路 RRF 合併
    → ACT-R 認知衰減重排（The Chronicle of Mneme）

The Chronicle of Mneme（mneme_weight.py）
    — ACT-R 認知衰減系統：記憶的激活強度 = f(使用頻率, 時間距離)
    — access_log: chronicle.db（SQLite），記錄每次搜尋/閱讀的存取
    — 公式：A_i = ln(Σ t_k^{-0.5})
    — 整合到 search pipeline 作為 rerank bonus

The Illumination（vectorize.py --contextualize）
    — Contextual Retrieval：為每個段落 chunk 加上語境化摘要
    — 解決切片斷章取義問題，讓 embedding 捕捉全局脈絡
    — 快取：contextual_cache.json（避免重複 LLM 呼叫）

PPR Spreading Activation（tapestry.py）
    — Personalized PageRank：以搜尋結果為 seed，在圖譜中擴散
    — 發現語義隱含相關但未被向量/BM25 直接命中的記憶
    — 流程：Kuzu 子圖 → NetworkX DiGraph → PPR → RRF 合併

The Rite of Slumber（slumber.py）
    — 記憶鞏固機制：定期整理記憶庫
    — Reflection：從近期記憶提煉高層次洞察 → 10_Profile/reflections/
    — Hebbian Learning：共現記憶強化 co_recalled 邊
    — The Lethe Protocol：策略性遺忘（dormant 標記），不刪除可恢復
```

### YAML enrichment 欄位

```yaml
# ── Enrichment（LLM 語意增強，僅含原文出現的實體）──
enriched_at: "2026-04-11T12:00:00"
importance: medium          # low / medium / high
period: "2025年某城市求職期"
themes: ["職涯", "轉變"]
personal_facts:             # 個人生活事實（非客觀知識）
  - "friend-A 住在 Tokyo"
entities:
  locations: ["Tokyo", "Osaka"]
  people: ["friend-A"]
  events: ["friend-A project"]
  emotions: ["期待"]
```

### Tapestry CLI 用法

```bash
# 從現有記憶庫重建 Tapestry（初次設定或 rebuild）
python3 00_System/ingest.py --weave-tapestry
python3 00_System/enrich.py --weave-tapestry   # 同等效果

# Tapestry 統計 / 搜尋測試
python3 00_System/tapestry.py --stats
python3 00_System/tapestry.py --search "Tokyo,friend-A"
python3 00_System/tapestry.py --backfill
```

### The Chronicle CLI 用法

```bash
# 存取紀錄統計
python3 00_System/mneme_weight.py --stats

# 最活躍的 10 個記憶
python3 00_System/mneme_weight.py --top 10

# 查詢特定記憶的 ACT-R 激活分數
python3 00_System/mneme_weight.py --score "30_Journal/2025/250604.md"
```

### Contextual Retrieval CLI 用法

```bash
# The Illumination — 為所有段落生成語境化摘要
python3 00_System/vectorize.py --contextualize

# 指定模型 + 重建所有
python3 00_System/vectorize.py --contextualize --ctx-model gemma3:4b --rebuild

# 生成語境化摘要後重建索引（完整流程）
python3 00_System/vectorize.py --contextualize && python3 00_System/vectorize.py --rebuild
```

### The Rite of Slumber CLI 用法

```bash
# 執行完整鞏固（三個儀式全做）
python3 00_System/slumber.py

# 僅反射（從近期記憶提煉洞察）
python3 00_System/slumber.py --reflect --days 14

# 僅赫布學習（共現記憶強化）
python3 00_System/slumber.py --hebbian

# 策略性遺忘（預覽模式）
python3 00_System/slumber.py --forget --dry-run

# 鞏固統計
python3 00_System/slumber.py --stats
```

---

## 未來可延伸的儀式設計

以下是**尚未實作但符合神話主題**的功能命名建議，開發時可參考：

- **The Call of the Muses** — Gap Analysis，當某位繆思的領域記憶太少，她會主動呼喚：
  *"Clio is silent about your early years. Tell me of the days before 2022."*

- **The Lethe Protocol** — 記憶過期 / 降溫機制，久未查詢的記憶沉入忘川

- **The Augury** — 定期搜尋品質報告，像占卜一樣給出健康診斷

- **The Vigil** — 背景監控 / watch.py，守夜等待新記憶進入

- **The Codex** — 結構化的 Profile 文件（10_Profile/），記憶女神的聖典

- **Echoes** — 相似記憶的關聯推薦，記憶在時間中留下的回響

---

## 開發守則

1. **Ground-Truth Preserving** — Oracle 永遠只記錄原文出現的事實，不推斷、不補充
2. **神話主題是裝飾，不是障礙** — 若某處加入主題會讓可讀性下降，保持工程優先
3. **命名一致性** — 新功能盡量沿用已有的神話詞彙（Spring, Oracle, Vault, Muse, Weaving...）
4. **儀式感體現在細節** — 不需要每行都有詩意，關鍵節點（啟動、完成、錯誤）有即可
