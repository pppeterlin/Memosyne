# Sample Vault Walkthrough

`sample_vault/` 是完全合成的示例記憶庫，用來：

1. 讓新使用者在不暴露任何私人資料的前提下，立即看到 Memosyne 各項功能。
2. 作為文件範例與 evaluation 測試的基準資料。
3. 作為貢獻者重現 bug 的最小可分享 fixture。

> **重要**：sample_vault 中所有人物、地點、事件、日期均為虛構，不是把真實資料匿名化的結果。提交 PR 時若需要新增 sample 內容，請延續這個原則。

---

## 主角設定

虛構主角 **Mira Chen**，1994 年生於台中、現居台北中山區的自由插畫家。

核心關係：
- **Aiko** — 大學同學，現為東京編輯
- **Ravi** — 工作室室友，平面設計師
- **Lin 老師** — 前指導教授，住新竹
- **Wei** — 青鳥咖啡老闆，2026 年初的客戶
- **Saturn** — 領養的英短貓

關鍵時間軸：
- 2026-01-12：青鳥咖啡提案修改（journal）
- 2026-02-03：收到 Aiko 邀約東京小誌第二集（journal）
- 2026-02-04：Tokyo Slow Vol.2 專案啟動（project）
- 2026-02-15：與 AI 討論東京冬天取材（ai_chat）
- 2026-02-22 → 2026-03-08：東京取材行程（project 中規劃）

---

## 目錄結構

```
sample_vault/
├── 10_Profile/             ← Polyhymnia（神聖詩歌：身份）
│   ├── bio.md              基本背景、住處、寵物、朋友
│   ├── career.md           學歷、工作軌跡、代表案件、工具
│   └── preferences.md      飲食、工作節奏、居住、旅行
├── 20_AI_Chats/            ← Calliope（史詩：對話）
│   └── Synthetic/
│       └── sample_chat.md  與 AI 討論東京冬天取材
├── 30_Journal/             ← Clio（歷史：日記）
│   └── 2026/
│       ├── 260112.md       週一日記：青鳥咖啡提案
│       └── 260203.md       週二日記：Aiko 邀約
├── 40_Projects/            ← Terpsichore（舞蹈：行動）
│   └── tokyo_slow_v2.md    Tokyo Slow Vol.2 專案
└── 50_Knowledge/           ← Urania（天文：知識）
    └── watercolor_notes.md 水彩冷暖色筆記
```

每份檔案的 frontmatter 都有 `muse:` 欄位明示對應的繆思女神，用以驗證 muse routing 是否正確。

---

## 對應功能展示

### A. Person / Place / Event 抽取

**查詢**：`memosyne search "Aiko"`

預期命中：
- `30_Journal/2026/260203.md`（直接提到邀約）
- `40_Projects/tokyo_slow_v2.md`（合作對象）
- `10_Profile/bio.md`（朋友圈背景）
- `20_AI_Chats/Synthetic/sample_chat.md`（對話中提到她）

可驗證：Tapestry 中應該有 `Aiko (person) → 出現於 → 多個 memory` 的邊。

### B. Temporal 查詢

**查詢**：`memosyne search "2026 年 2 月做了什麼"`

預期命中該月份的 journal 與 project 檔。可用於驗證 period extraction 與 temporal rerank。

### C. AI Chat vs Journal 區分

**查詢**：`memosyne search "東京冬天的取材方向"`

- 應該優先命中 `20_AI_Chats/Synthetic/sample_chat.md`（直接討論）
- 其次是 `40_Projects/tokyo_slow_v2.md`（專案規劃也提到）

可驗證：Calliope 與 Terpsichore 兩位繆思的 centroid 區隔。

### D. Graph 關聯查詢

**查詢**：`memosyne search "Tokyo Slow"` 或 tapestry walk from `Aiko`

預期 Tapestry 能展開：
```
Aiko → involved_in → Tokyo Slow Vol.2 → mentions → 谷根千
                                    → mentions → 2026-02-22 出發
```

可驗證：跨檔案的 graph spreading activation 能找到只在 project 提及但 journal 沒寫的細節（例如行程日期）。

### E. Correction Flow

**情境**：故意把 `bio.md` 中 Saturn 的領養日期改錯，跑 `memosyne correct` 預演修正流程：

```bash
memosyne correct --dry-run "把 Saturn 領養日期從 2023 年 3 月改為 2023 年 5 月"
```

可驗證：dry-run diff 輸出、aletheia_log 記錄、reembed queue 排程。

### F. Negative / Abstention Query

**查詢**：`memosyne search "Mira 的弟弟叫什麼名字"`

vault 中沒有任何兄弟姐妹資訊。預期行為：
- 檢索結果應該為低分或空
- 若接 LLM（Oracle 模式），應回答「資料中未提及」而非編造

這個 query 用來驗證 Oracle 的 ground-truth-preserving 守則。

---

## 在 Sample Vault 上跑 Memosyne

```bash
# 兩個 env var 一起設：資料來源 + 衍生 artifacts 寫到哪
export MEMOSYNE_VAULT_DIR=$PWD/sample_vault
export MEMOSYNE_ARTIFACT_DIR=$PWD/sample_vault/_artifacts

# 健檢（衍生 artifacts 一開始全是 fail，正常）
python memosyne.py rebuild
python memosyne.py health
python memosyne.py search "Aiko"
```

> **注意**：`MEMOSYNE_VAULT_DIR` 切換的是 Memosyne 看的「資料根目錄」（包含 `10_Profile/` 等繆思資料夾的目錄）。`MEMOSYNE_ARTIFACT_DIR` 進一步把所有衍生 artifacts（ChromaDB、BM25 index、Tapestry graph、caches）導到指定路徑——sample 模式建議指向 `sample_vault/_artifacts/`（已 gitignored）以免與你的私有索引混在一起。
>
> 兩個 env var 都不設時，Memosyne 走預設的 `Personal_Brain_DB/` 路徑（含 symlink shim 與 `_vault` submodule）。

### 驗證 MCP server 可用

`tests/test_mcp_smoke.py` 在 sample_vault 的索引上跑一次 stdio 端對端測試：spawn `mcp_server.py`、initialize、`tools/list`、實際呼叫 `search_memory` 與 `get_memory_health`，沒有任何 LLM 呼叫，純離線。

```bash
# 需要先跑過上面的 rebuild 把 sample 索引建起來
python tests/test_mcp_smoke.py
```

Pass 條件：14 個 tools 註冊成功、`search_memory("Aiko")` 結果含 sample_chat 命中、`get_memory_health` 有回內容。

---

## Evaluation Sample Set

`sample_vault/` 應搭配一組迷你 golden questions，路徑建議 `sample_vault/_eval/golden.yaml`。每題包含 question + expected source file。

最小集合（v0.4 待補）：
- "Aiko 是誰？" → `10_Profile/bio.md`
- "Tokyo Slow Vol.2 出發日期？" → `40_Projects/tokyo_slow_v2.md`
- "冷色為主的水彩配方？" → `50_Knowledge/watercolor_notes.md`
- "Mira 對什麼食物過敏？" → `10_Profile/preferences.md`
- "誰是青鳥咖啡的老闆？" → `30_Journal/2026/260112.md`
- "Mira 的弟弟叫什麼？" → `<abstain>`（測試 negative query）

跑法（待 v0.4 完成）：
```bash
memosyne eval --sample
```

---

## 擴充原則

要往 sample vault 加內容時：

1. 角色固定為 Mira Chen 與其關係網。不要引入第二位無關主角，避免 graph 變得難讀。
2. 任何新加的人物 / 地點 / 公司必須是虛構的，且不可與真實知名人物重名。
3. 日期保持在 2026 年內，避免跨年增加時間複雜度。
4. 每個檔案保持 50–150 行；sample 不需展示真實 vault 的長度。
5. 若新增的內容是為了 demo 某個新功能，請在本文件對應功能段落補上 query 與預期行為。
