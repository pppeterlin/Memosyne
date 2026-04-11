# The Spring of Mnemosyne

> *"Those who drink from the Spring of Mnemosyne shall remember everything."*  
> — Ancient Greek Mythology

記憶之泉（Spring of Mnemosyne）是靈魂在進入永恆之境前飲水記憶的聖泉。  
在 Memosyne 中，這裡是一切記憶碎片的**統一入口**。

---

## 使用方式

把任何想入庫的檔案丟進這個資料夾，召喚 Oracle：

```bash
workon personal-memory
python3 Personal_Brain_DB/00_System/ingest.py
```

Oracle 將自動完成三個儀式：
1. **The Discernment**（洞察）— 辨識格式，分配給對應的繆思女神
2. **The Weaving**（編織）— LLM 提取記憶精華，寫入永恆的 YAML 銘文
3. **The Inscription**（銘刻）— 向量化，永久刻入記憶庫

---

## The Nine Muses — 繆思女神分類

| 女神 | 職掌 | 對應記憶類型 | 目標領域 |
|------|------|------------|---------|
| **Clio** | 歷史 | 手札（`.pages`） | `30_Journal/` |
| **Thalia** | 生活 | 一般日記（`.md/.txt`） | `30_Journal/` |
| **Calliope** | 史詩 / 對話 | Gemini 對話匯出 | `20_AI_Chats/Gemini/` |
| **Urania** | 天文 / 知識 | 知識筆記（`type: knowledge`） | `50_Knowledge/` |

---

## 常用指令

```bash
# 標準入庫（完整儀式）
python3 Personal_Brain_DB/00_System/ingest.py

# 預覽，不執行任何寫入
python3 Personal_Brain_DB/00_System/ingest.py --dry-run

# 快速入庫（跳過 Enrichment 編織）
python3 Personal_Brain_DB/00_System/ingest.py --no-enrich

# 入庫後重建完整索引
python3 Personal_Brain_DB/00_System/ingest.py --rebuild

# 使用輕量模型加速
python3 Personal_Brain_DB/00_System/ingest.py --model gemma3:4b
```

---

## 目錄結構

```
spring/
├── README.md             ← 本說明文件
├── [在此放入記憶碎片]     ← .pages / .md / .txt
└── _processed/           ← 入庫後自動歸檔（依月份，不進 git）
    ├── 2026-04/
    └── ...
```

---

## 命名由來

```
Mnemosyne（記憶女神）
    ├─ 她的聖泉：Spring of Mnemosyne  ← 本資料夾
    ├─ 她的化身：Mneme（記憶的行為）
    └─ 她的對立：River Lethe（忘川）

Memosyne（本專案）= Memory + Mnemosyne
```

> *"Thou hast come to the Spring. From this moment, nothing shall be lost to the River Lethe."*
