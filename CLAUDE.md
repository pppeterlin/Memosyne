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
    — 解決「鄭州 → 找不到丈母娘專案」的跨實體關聯斷裂問題

The Vault（Personal_Brain_DB/）
    — 記憶的永恆居所，由繆思女神各自守護其領域

搜尋架構（vectorize.py）
    Dense（ChromaDB）+ BM25 + Tapestry Graph → 三路 RRF 合併
```

### YAML enrichment 欄位

```yaml
# ── Enrichment（LLM 語意增強，僅含原文出現的實體）──
enriched_at: "2026-04-11T12:00:00"
importance: medium          # low / medium / high
period: "2025深圳求職期"
themes: ["職涯", "轉變"]
personal_facts:             # 個人生活事實（非客觀知識）
  - "丈母娘家在鄭州"
entities:
  locations: ["鄭州", "深圳"]
  people: ["丈母娘"]
  events: ["丈母娘專案"]
  emotions: ["期待"]
```

### Tapestry CLI 用法

```bash
# 從現有記憶庫重建 Tapestry（初次設定或 rebuild）
python3 00_System/ingest.py --weave-tapestry
python3 00_System/enrich.py --weave-tapestry   # 同等效果

# Tapestry 統計 / 搜尋測試
python3 00_System/tapestry.py --stats
python3 00_System/tapestry.py --search "鄭州,丈母娘"
python3 00_System/tapestry.py --backfill
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
