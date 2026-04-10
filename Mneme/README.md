# Mneme — 記憶入口

> **Mneme**（Μνήμη）是希臘神話中記憶女神 **Mnemosyne** 的化身之一，  
> 代表記憶的行為本身（the act of remembering）。  
> 這個資料夾是 Memosyne 記憶庫的統一入口。

---

## 使用方式

把任何想入庫的檔案丟進這個資料夾，然後執行：

```bash
workon personal-memory
python3 Personal_Brain_DB/00_System/ingest.py
```

就完成了。腳本會自動：
1. 偵測格式 → 歸檔至正確目錄
2. LLM Enrichment（提取實體、地點、情緒）
3. 更新向量索引（Dense + BM25）

---

## 支援格式

| 格式 | 偵測邏輯 | 目標位置 |
|------|---------|---------|
| `.pages` | Apple Pages 手札 | `30_Journal/{YEAR}/` |
| `.md` (Gemini 匯出) | 含「備份時間：」或 `_hash` 檔名 | `20_AI_Chats/Gemini/` |
| `.md` (日記) | 檔名含日期（`YYMMDD`）或一般文字 | `30_Journal/{YEAR}/` |
| `.md` (知識筆記) | frontmatter `type: knowledge` | `50_Knowledge/` |
| `.txt` | 任何純文字 | `30_Journal/{YEAR}/` |

---

## 常用指令

```bash
# 標準入庫
python3 Personal_Brain_DB/00_System/ingest.py

# 預覽（不執行任何寫入）
python3 Personal_Brain_DB/00_System/ingest.py --dry-run

# 快速入庫（跳過 Enrichment，只歸檔＋向量化）
python3 Personal_Brain_DB/00_System/ingest.py --no-enrich

# 入庫後重建完整索引（而非增量更新）
python3 Personal_Brain_DB/00_System/ingest.py --rebuild

# 用輕量模型加速 Enrichment
python3 Personal_Brain_DB/00_System/ingest.py --model gemma3:4b
```

---

## 資料夾結構

```
Mneme/
├── README.md          ← 本說明文件
├── *.pages / *.md     ← 把檔案丟在這裡
└── _processed/        ← 處理後自動歸檔（依月份分組）
    ├── 2026-04/
    └── ...
```

> `_processed/` 已加入 `.gitignore`，不會被版本控制追蹤。

---

## 命名由來

```
Mnemosyne（記憶女神）
    └─ Mneme（記憶的行為）  ← 本資料夾
    └─ Melete（沉思）
    └─ Aoide（歌唱）

Memosyne（本專案）= Memory + Mnemosyne
```
