"""分段 enrich + 合併邏輯測試（長檔個人 profile 入庫）。

確保超長檔被切成 ≤ENRICH_SEGMENT_CHARS 的段落、各段在自然邊界切割，
合併時 entities/themes/personal_facts 取聯集去重、importance 取最高、
period/chat_category 取第一個非空。短檔行為須與舊版完全一致。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import enrich  # noqa: E402


def test_short_content_single_segment():
    """短於門檻的內容回傳單元素（行為不變）。"""
    assert enrich._split_for_enrich("hello world") == ["hello world"]


def test_long_content_split_under_limit():
    """超長內容切成多段，每段不超過 ENRICH_SEGMENT_CHARS。"""
    text = "x" * 16000
    segs = enrich._split_for_enrich(text)
    assert len(segs) > 1
    assert all(len(s) <= enrich.ENRICH_SEGMENT_CHARS for s in segs)
    assert "".join(segs) == text  # 無遺漏


def test_split_prefers_heading_boundaries():
    """切點優先落在標題行，不切斷段落。"""
    sections = [f"## 章節 {i}\n" + ("内容文字。" * 200) for i in range(6)]
    text = "\n\n".join(sections)
    segs = enrich._split_for_enrich(text)
    assert all(len(s) <= enrich.ENRICH_SEGMENT_CHARS for s in segs)
    # 多數段落應以標題開頭（自然邊界）
    assert sum(s.lstrip().startswith("##") for s in segs) >= len(segs) - 1


def test_merge_unions_and_dedups():
    merged = enrich._merge_enrichments([
        {"entities": {"locations": ["Tokyo"], "people": ["A"], "events": [], "emotions": ["喜"]},
         "themes": ["t1"], "period": "", "importance": "low",
         "personal_facts": ["f1"], "chat_category": ""},
        {"entities": {"locations": ["Tokyo", "Osaka"], "people": ["B"], "events": ["e"], "emotions": []},
         "themes": ["t2"], "period": "2025求職", "importance": "high",
         "personal_facts": ["f2", "f1"], "chat_category": "personal"},
    ])
    assert merged["entities"]["locations"] == ["Tokyo", "Osaka"]  # 去重保序
    assert merged["entities"]["people"] == ["A", "B"]
    assert merged["importance"] == "high"                          # 取最高
    assert merged["period"] == "2025求職"                          # 第一個非空
    assert merged["personal_facts"] == ["f1", "f2"]                # 去重保序
    assert merged["chat_category"] == "personal"


def test_merge_caps_lengths():
    """themes ≤6、personal_facts ≤20。"""
    parts = [{"entities": {"locations": [], "people": [], "events": [], "emotions": []},
              "themes": [f"theme{i}" for i in range(10)],
              "period": "", "importance": "medium",
              "personal_facts": [f"fact{i}" for i in range(30)],
              "chat_category": ""}]
    merged = enrich._merge_enrichments(parts)
    assert len(merged["themes"]) == 6
    assert len(merged["personal_facts"]) == 20
