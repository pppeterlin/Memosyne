# Benchmark Guide

Memosyne 的檢索評估分成兩條線：

- **Eternal Mirror**：從 HyQE cache 抽樣，自監督檢查檢索是否能找回來源記憶。
- **Augury**：人工 golden set，用少量高品質問題檢查真實使用意圖。

兩者互補。Eternal Mirror 適合穩定追蹤 regression；Augury 適合檢查實際使用體驗。

## 隱私規則

Committed benchmark templates 只能使用 synthetic examples。

本機私有 `golden_set.yaml` 可以使用真實評估題，但不得提交。文檔與示例資料遵守 repo 根目錄 `AGENTS.md` 的 workspace-level 規範。

## Eternal Mirror

從 `hyqe_cache.json` 抽樣問題，檢查 `search()` 是否能在 top-K 找回來源 path。

```bash
workon personal-memory
cd Personal_Brain_DB/00_System
python benchmark/retrieval_eval.py --config baseline --n 500 --diff
python benchmark/retrieval_eval.py --config full --n 500 --hygiene --diff
```

常用參數：

- `--config baseline`：不啟用 muse routing 的參考基準。
- `--config full`：目前完整檢索設定。
- `--hygiene`：排除 `view=hyqe`，降低自我命中資料洩漏。
- `--stratify-by muse|length|both`：依繆思或問題長度分層抽樣。
- `--fail-on-regression`：搭配 `--diff`，退步超過閾值時 exit 1。

CI 風格檢查：

```bash
make eval-ci
```

## Augury Golden Set

建立本機私有 golden set：

```bash
cp benchmark/golden_set.example.yaml benchmark/golden_set.yaml
```

編輯 `benchmark/golden_set.yaml`，每題至少包含：

```yaml
Clio:
  - query: "sample journal 的主要情緒是什麼"
    expected_paths:
      - "30_Journal/2026/sample_journal.md"
    tags: [synthetic, journal]
    notes: "測試日記情緒召回"
```

執行混合評估：

```bash
python benchmark/retrieval_eval.py \
  --config full \
  --hygiene \
  --golden-set golden_set.yaml \
  --diff
```

報告會輸出到 `benchmark/reports/`，並在 markdown 中並列：

- Eternal Mirror 指標
- Augury 指標
- 分繆思領域統計
- 分問題長度統計

## v0.2 Freeze 建議

v0.2 freeze 前至少保留一份完整報告：

```bash
python benchmark/retrieval_eval.py \
  --config full \
  --n 500 \
  --hygiene \
  --stratify-by both \
  --diff
```

若已建立私有 golden set，另跑：

```bash
python benchmark/retrieval_eval.py \
  --config full \
  --hygiene \
  --golden-set golden_set.yaml \
  --diff
```
