# Memosyne — Project Rules

## 唯一目標

把一個人的記憶存成**任何 AI Agent 都讀得懂的純 Markdown**，而且能原封不動帶到下一個模型。

所有設計決策以此為準：**Vault 是資產，其餘皆為衍生。** 一個功能如果不能讓記憶更容易被寫入、被讀懂或被信任，就不屬於 core。

---

## 核心靈感：希臘神話記憶女神 Mnemosyne

本專案名稱 **Memosyne** = Memory + Mnemosyne（記憶女神）。
「記憶入庫」不只是資料處理，而是一場**儀式**。

```
Mnemosyne（泰坦神，記憶女神）
    ├── 她的聖泉：Spring of Mnemosyne — 飲此泉水，靈魂記憶永不遺失
    ├── 她的對立：River Lethe（忘川）  — 飲此水，遺忘一切
    └── 她的九個女兒：The Nine Muses（繆思女神）
            ├── Polyhymnia  神聖詩歌  → 10_Profile（身份）
            ├── Calliope    史詩/對話 → 20_AI_Chats
            ├── Clio        歷史      → 30_Journal（日記）
            ├── Terpsichore 舞蹈      → 40_Projects（行動）
            └── Urania      天文/知識 → 50_Knowledge
```

---

## 命名規範（Naming Convention）

**程式碼用工程名，神話留給人看的地方。**

- 模組、函式、變數、環境變數、artifact key：直接說明它做什麼（`access_log.py`、`iter_memory_files()`、`MEMOSYNE_SEARCH_PROFILE`）。讀程式的人不應該需要查對照表。
- CLI 指令、輸出訊息、文件的敘事與 glossary：可以用神話名（`memosyne slumber`、"The Oracle faltered."）。
- 既有的資料檔名（`tapestry_db`、`chronicle.db`、`aletheia_log.jsonl`…）不改名——那是使用者 vault 裡的相容性契約。

| 概念 | 對使用者的名字 | 程式碼 |
|---|---|---|
| 資料入口 | The Spring | `paths.spring_dir()`、`ingest.py` |
| 記憶本體 | The Vault | `paths.data_root()` |
| LLM 語意增強 | The Oracle / The Weaving | `enrich.py` |
| 全貌地圖 | The Digest | `digest.py` |
| 存取紀錄 | The Chronicle of Mneme | `access_log.py` |
| 缺口提問 | The Call of the Muses | `gap_questions.py` |
| 實體圖譜 | The Tapestry | `labs/graph.py` |
| 領域路由 | The Muses | `labs/routing.py` |
| 記憶鞏固 | The Rite of Slumber | `labs/consolidate.py` |
| 審計 / 更正 | The Augury / Aletheia | `labs/audit.py`、`labs/corrections.py` |
| 遺忘 | Lethe | `dormant` frontmatter 標記 |

完整模組對照見 `docs/architecture.md`。

---

## CLI / 輸出風格

程式的輸出訊息可以帶有儀式感，但**不強制**——在不影響可讀性的前提下加入。

- 流程開始 → 召喚感（"The Spring stirs..."）
- 完成 → 永恆感（"Nothing lost to Lethe."）
- 錯誤 → 人性化但莊重（"The Oracle faltered."），並且一定附上下一步該怎麼做
- 空狀態 → 寧靜感（"The waters are still."）

```
❌ Processing complete. 3 files added.
✅ 🌊 3 memory fragments have found their eternal place.

❌ Error: LLM failed for file.md
✅ The Oracle faltered for: file.md
```

---

## 架構：三層

```
core    ingest → enrich → digest → keyword search → MCP        永遠安裝
search  dense vector retrieval（ChromaDB + embeddings）          pip install -e ".[search]"
labs    graph、ACT-R、routing、consolidation、audit、corrections  memosyne.labs，opt-in
```

- 預設搜尋路徑只有 dense（有裝才用）+ BM25 → RRF。其餘階段在 `memosyne.labs`，由 `MEMOSYNE_SEARCH_PROFILE=full` 開啟。這個預設來自 `docs/evaluation.md` 的 ablation，不是偏好。
- 列舉型與時間軸問題交給 digest（`<vault>/_digest/`），不是交給檢索。
- AI 對話紀錄只在明確要求時進入搜尋範圍。

---

## 開發環境

驗證專案狀態請用專案自己的 virtualenv，不要用裸 `python3` 的結果下判斷：

```bash
memosyne health
make verify
```

本機開發者可在不進 git 的 `AGENTS.local.md` 記錄個人環境路徑、virtualenv 名稱、alias 與私有操作習慣。若該檔存在，agent 應優先讀取並遵守；但不得把其中內容複製到公開文檔。

---

## 隱私規範（程式碼、文檔與示例一體適用）

這個 repo 是公開的。以下規範適用於程式碼、註解、測試、README、docs、sample vault、commit message。

- 只使用合成資料：通用地名、通用場景、`sample_vault/` 裡的虛構人物。
- 不得寫入真實人名、暱稱、寵物名、居住地、雇主，或任何可回推個人經歷的組合——**包含程式碼裡的關鍵字清單與 docstring 範例**。個人化詞彙放在 vault 內的設定檔（如 `<vault>/tags.yaml`）。
- 不得複製私有記憶的原文、標題、摘要或真實查詢。
- 不得 commit 任何由真實 vault 生成的檔案：索引、快取、圖譜匯出、評估報告、擷取的查詢。**刪除不會讓它從 git 歷史消失。**
- 含個人路徑、API key 位置、機器設定的內容，只放在 gitignored 的 local override。
- `make verify` 會跑三道檢查；其中 `check_no_private_names.sh` 需要本機的 `.private_terms` 禁字清單才會生效，沒有就等於沒檢查。

---

## 記憶的格式

```yaml
---
title: "Tokyo Slow 第二集邀約"
date: 2026-02-03
type: journal
# ── Enrichment（LLM 語意增強，僅含原文出現的實體）──
enriched_at: "2026-02-04T09:00:00"
importance: high            # low / medium / high
period: "2026 冬季接案期"
themes: ["旅行", "工作"]
personal_facts:             # 個人生活事實（非客觀知識）
  - "Aiko 住在東京"
entities:
  locations: ["東京", "冰島"]
  people: ["Aiko", "Ravi", "Wei"]
  events: ["《Tokyo Slow》第二集的合作"]
  emotions: []
---
```

---

## 常用指令

```bash
memosyne ingest                    # The Spring Ritual：分類 → 增強 → 索引 → 彙整
memosyne search "…" [--include-chats] [--profile full]
memosyne digest [--print timeline|entities]
memosyne rebuild [--full]
memosyne call                      # The Call of the Muses
memosyne chronicle --stats         # 存取紀錄
memosyne mcp [--print-config|--check|--http]
memosyne health

# labs
memosyne slumber [--reflect|--hebbian|--forget --dry-run|--stats]
memosyne tapestry [--stats|--search "A,B"|--backfill]
memosyne audit / correct / contextualize / hyqe / query-log / chat
```

每個模組也可以 `python -m memosyne.<module> --help` 單獨執行。

---

## 開發守則

1. **Ground-Truth Preserving** — Oracle 只記錄原文出現的事實，不推斷、不補充。非字面抽取的 LLM 產物（aggregates、reflections）必須放在明確分開的地方。
2. **Vault 是唯一事實來源** — 任何索引都必須能從 Markdown 重建。
3. **分層不可逆向** — core 模組不得在 module level import `memosyne.labs` 或 search extra 的套件；一律在函式內、功能開關後 lazy import。
4. **路徑只由 `paths.py` 決定** — 不得用 `__file__` 推算資料位置。
5. **能不用 LLM 就不用** — 可以決定性完成的事（digest、缺口分析、連結抽取）就不要依賴 LLM 或索引。
6. **預設值要有證據** — 檢索階段要成為預設，必須有評估證明它有幫助；沒有證據就放 labs。
7. **神話是裝飾，不是障礙** — 加入主題若讓可讀性下降，工程優先。
8. **儀式感體現在細節** — 關鍵節點（啟動、完成、錯誤）有即可，不需要每行都有詩意。

---

## 尚未實作、符合主題的方向

- **Echoes** — 相似記憶的關聯推薦，記憶在時間中留下的回響
- **The Vigil** — 背景守夜，新檔案進入 spring 時自動入庫
- 更多來源的 parser（其他 AI 助理的匯出、通訊軟體紀錄）
