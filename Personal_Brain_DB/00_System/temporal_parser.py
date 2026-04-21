#!/usr/bin/env python3
"""
Memosyne — The Unfolding Question: Temporal Parser

從查詢中抽取時間錨點，轉換為具體日期範圍。
用於 search() 的時間過濾與時間距離加權。

支援格式：
  絕對時間：「2023年」「2023年夏天」「2025-06」「去年3月」
  相對時間：「上個月」「去年」「前年」「最近」「三個月前」
  季節：    「春天」「夏天」「秋天」「冬天」
  模糊：    「最近」「近期」→ 最近 30 天

用法：
  from temporal_parser import extract_time_range, time_distance_bonus

  # 從 query 抽取時間範圍
  tr = extract_time_range("我 2023 年夏天在 Tokyo 做了什麼")
  # → TimeRange(start="2023-06-01", end="2023-08-31", anchor="2023-07-15")

  # 計算時間距離 bonus
  bonus = time_distance_bonus("2023-07-20", tr)
  # → 0.95（越接近 anchor 越高）
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class TimeRange:
    """時間範圍結果。"""
    start: str          # YYYY-MM-DD
    end: str            # YYYY-MM-DD
    anchor: str         # 範圍中點，用於距離計算
    raw_match: str      # 原始匹配文字
    confidence: float   # 0.0–1.0


# ─── 季節定義 ────────────────────────────────────────────────

SEASONS = {
    "春": (3, 5),
    "夏": (6, 8),
    "秋": (9, 11),
    "冬": (12, 2),   # 跨年：12月–隔年2月
}

SEASON_ALIASES = {
    "春天": "春", "春季": "春", "春": "春",
    "夏天": "夏", "夏季": "夏", "夏": "夏",
    "秋天": "秋", "秋季": "秋", "秋": "秋",
    "冬天": "冬", "冬季": "冬", "冬": "冬",
}

# ─── 相對時間詞彙 ────────────────────────────────────────────

RELATIVE_WORDS = {
    "今年":   0,
    "去年":  -1,
    "前年":  -2,
    "大前年": -3,
}

MONTH_RELATIVE = {
    "上個月": -1, "上月": -1,
    "這個月":  0, "本月":  0,
    "前兩個月": -2,
    "前三個月": -3,
}


def _last_day(year: int, month: int) -> int:
    """回傳某月的最後一天。"""
    import calendar
    return calendar.monthrange(year, month)[1]


def _make_range(y: int, m_start: int, m_end: int, raw: str,
                confidence: float = 0.9) -> TimeRange:
    """從年月範圍建立 TimeRange。"""
    # 處理冬季跨年
    if m_start > m_end:
        end_year = y + 1
    else:
        end_year = y

    start = f"{y:04d}-{m_start:02d}-01"
    end = f"{end_year:04d}-{m_end:02d}-{_last_day(end_year, m_end):02d}"

    # anchor = 範圍中點
    d_start = datetime.strptime(start, "%Y-%m-%d")
    d_end = datetime.strptime(end, "%Y-%m-%d")
    d_anchor = d_start + (d_end - d_start) / 2
    anchor = d_anchor.strftime("%Y-%m-%d")

    return TimeRange(start=start, end=end, anchor=anchor,
                     raw_match=raw, confidence=confidence)


# ─── 主解析函式 ──────────────────────────────────────────────

def extract_time_range(query: str) -> Optional[TimeRange]:
    """
    從查詢字串中抽取時間範圍。

    優先級：
    1. 完整日期 YYYY-MM-DD
    2. 年月 YYYY-MM 或 YYYY年M月
    3. 年+季節 YYYY年夏天
    4. 單獨年份 YYYY年
    5. 相對時間 去年/上個月/最近
    6. 相對年份+季節 去年夏天
    7. 相對年份+月份 去年3月
    """
    now = datetime.now()

    # ── 1. 完整日期 YYYY-MM-DD ────────────────────────────────
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', query)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            date_str = f"{y:04d}-{mo:02d}-{d:02d}"
            return TimeRange(start=date_str, end=date_str, anchor=date_str,
                             raw_match=m.group(0), confidence=1.0)

    # ── 2. 年月 YYYY-MM 或 YYYY年M月 ─────────────────────────
    m = re.search(r'(\d{4})[-/](\d{1,2})(?!\d)', query)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1900 <= y <= 2100 and 1 <= mo <= 12:
            return _make_range(y, mo, mo, m.group(0), confidence=0.95)

    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月', query)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1900 <= y <= 2100 and 1 <= mo <= 12:
            return _make_range(y, mo, mo, m.group(0), confidence=0.95)

    # ── 3. 年+季節 YYYY年夏天 ─────────────────────────────────
    for alias, season_key in SEASON_ALIASES.items():
        pattern = rf'(\d{{4}})\s*年?\s*{re.escape(alias)}'
        m = re.search(pattern, query)
        if m:
            y = int(m.group(1))
            if 1900 <= y <= 2100:
                m_start, m_end = SEASONS[season_key]
                return _make_range(y, m_start, m_end, m.group(0), confidence=0.85)

    # ── 4. 單獨年份 YYYY年 或 YYYY ───────────────────────────
    m = re.search(r'(\d{4})\s*年', query)
    if m:
        y = int(m.group(1))
        if 1900 <= y <= 2100:
            return _make_range(y, 1, 12, m.group(0), confidence=0.7)

    # 裸 4 位數字（前後非數字）
    m = re.search(r'(?<!\d)(\d{4})(?!\d)', query)
    if m:
        y = int(m.group(1))
        if 2000 <= y <= 2100:
            return _make_range(y, 1, 12, m.group(0), confidence=0.5)

    # ── 5. 相對年份+季節 去年夏天 ─────────────────────────────
    for rel_word, offset in RELATIVE_WORDS.items():
        for alias, season_key in SEASON_ALIASES.items():
            pattern = rf'{re.escape(rel_word)}\s*{re.escape(alias)}'
            m = re.search(pattern, query)
            if m:
                y = now.year + offset
                m_start, m_end = SEASONS[season_key]
                return _make_range(y, m_start, m_end, m.group(0), confidence=0.8)

    # ── 6. 相對年份+月份 去年3月 ──────────────────────────────
    for rel_word, offset in RELATIVE_WORDS.items():
        pattern = rf'{re.escape(rel_word)}\s*(\d{{1,2}})\s*月'
        m = re.search(pattern, query)
        if m:
            y = now.year + offset
            mo = int(m.group(1))
            if 1 <= mo <= 12:
                return _make_range(y, mo, mo, m.group(0), confidence=0.85)

    # ── 7. 相對年份（單獨）去年/前年 ─────────────────────────
    for rel_word, offset in RELATIVE_WORDS.items():
        if rel_word in query:
            y = now.year + offset
            return _make_range(y, 1, 12, rel_word, confidence=0.65)

    # ── 8. 相對月份 上個月/這個月 ─────────────────────────────
    for rel_word, offset in MONTH_RELATIVE.items():
        if rel_word in query:
            target = now.replace(day=1)
            # 月份偏移
            mo = target.month + offset
            y = target.year
            while mo < 1:
                mo += 12
                y -= 1
            while mo > 12:
                mo -= 12
                y += 1
            return _make_range(y, mo, mo, rel_word, confidence=0.9)

    # ── 9. N 個月前 ──────────────────────────────────────────
    m = re.search(r'(\d+)\s*個?\s*月前', query)
    if m:
        months_ago = int(m.group(1))
        target = now.replace(day=1)
        mo = target.month - months_ago
        y = target.year
        while mo < 1:
            mo += 12
            y -= 1
        return _make_range(y, mo, mo, m.group(0), confidence=0.8)

    # ── 10. 最近/近期 → 最近 30 天 ───────────────────────────
    if re.search(r'最近|近期|近來|這陣子', query):
        end = now.strftime("%Y-%m-%d")
        start_dt = now - timedelta(days=30)
        start = start_dt.strftime("%Y-%m-%d")
        anchor = (now - timedelta(days=15)).strftime("%Y-%m-%d")
        return TimeRange(start=start, end=end, anchor=anchor,
                         raw_match="最近", confidence=0.6)

    return None


# ─── 時間距離 bonus ──────────────────────────────────────────

def time_distance_bonus(
    memory_date: str,
    time_range: TimeRange,
    max_bonus: float = 0.3,
    decay_days: float = 365.0,
) -> float:
    """
    計算記憶日期與查詢時間錨點的距離 bonus。

    越接近 anchor → bonus 越高（最高 max_bonus）。
    超出 time_range 範圍的記憶仍有微弱 bonus（衰減）。

    Args:
        memory_date: 記憶的日期 (YYYY-MM-DD)
        time_range: 查詢的時間範圍
        max_bonus: 最大加分值
        decay_days: 衰減半衰期（天）

    Returns:
        0.0 ~ max_bonus 的 bonus 值
    """
    if not memory_date or len(memory_date) < 10:
        return 0.0

    try:
        d_mem = datetime.strptime(memory_date[:10], "%Y-%m-%d")
        d_anchor = datetime.strptime(time_range.anchor, "%Y-%m-%d")
    except (ValueError, TypeError):
        return 0.0

    days_diff = abs((d_mem - d_anchor).days)

    # 在範圍內 → 高 bonus
    try:
        d_start = datetime.strptime(time_range.start, "%Y-%m-%d")
        d_end = datetime.strptime(time_range.end, "%Y-%m-%d")
        if d_start <= d_mem <= d_end:
            # 範圍內：根據與 anchor 的距離微調
            range_days = max((d_end - d_start).days, 1)
            closeness = 1.0 - (days_diff / range_days) * 0.3
            return max_bonus * closeness * time_range.confidence
    except (ValueError, TypeError):
        pass

    # 範圍外：指數衰減
    import math
    decay = math.exp(-days_diff / decay_days)
    return max_bonus * decay * 0.5 * time_range.confidence


def apply_temporal_rerank(
    results: list[dict],
    time_range: TimeRange,
) -> list[dict]:
    """
    對搜尋結果套用時間距離 bonus 並重排。

    修改每個 result 的 score，加上 time_distance_bonus。
    """
    for r in results:
        date = r.get("date", "")
        bonus = time_distance_bonus(date, time_range)
        r["temporal_bonus"] = round(bonus, 4)
        r["score"] = r.get("score", 0) + bonus

    results.sort(key=lambda x: -x.get("score", 0))
    return results


def filter_by_time_range(
    results: list[dict],
    time_range: TimeRange,
    strict: bool = False,
) -> list[dict]:
    """
    依時間範圍過濾搜尋結果。

    Args:
        strict: True = 只保留範圍內的結果；False = 保留全部但加 bonus
    """
    if not strict:
        return apply_temporal_rerank(results, time_range)

    filtered = []
    for r in results:
        date = r.get("date", "")
        if not date or len(date) < 10:
            continue
        if time_range.start <= date <= time_range.end:
            filtered.append(r)

    return apply_temporal_rerank(filtered, time_range)


# ─── CLI ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    test_queries = [
        "我 2023 年夏天在 Tokyo 做了什麼",
        "去年冬天的心情如何",
        "上個月寫了什麼日記",
        "2025-06-15 發生了什麼事",
        "前年3月的工作狀況",
        "最近有什麼重要的事",
        "三個月前的記憶",
        "2024年的職涯變化",
    ]

    if len(sys.argv) > 1:
        test_queries = [" ".join(sys.argv[1:])]

    for q in test_queries:
        tr = extract_time_range(q)
        if tr:
            print(f"  Q: {q}")
            print(f"     → {tr.start} ~ {tr.end}  anchor={tr.anchor}  "
                  f"conf={tr.confidence:.2f}  match={tr.raw_match!r}")
        else:
            print(f"  Q: {q}")
            print(f"     → (no temporal anchor)")
        print()
