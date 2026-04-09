#!/usr/bin/env python3
"""
Personal Brain DB — 檔案處理主腳本
功能：
  A. 處理 Gemini 對話：新增 YAML frontmatter、自動生成標題
  B. 處理 .pages 手札：解壓縮、提取文字、轉存為 .md
  C. 生成 00_System/index.json 語義索引

執行方式：
  python3 process_files.py              # 處理所有新檔案
  python3 process_files.py --reindex    # 只重新生成 index.json
  python3 process_files.py --all        # 強制重新處理所有檔案
"""

import os
import re
import json
import uuid
import hashlib
import zipfile
import argparse
from pathlib import Path
from datetime import datetime

# ─── 路徑設定 ───────────────────────────────────────────────
BASE = Path(__file__).parent.parent
GEMINI_SRC = BASE.parent / "gemini chat"
NOTES_SRC  = BASE.parent / "notes"
GEMINI_DST = BASE / "20_AI_Chats" / "Gemini"
JOURNAL_DST = BASE / "30_Journal"
SYSTEM_DIR  = BASE / "00_System"

try:
    import snappy
    HAS_SNAPPY = True
except ImportError:
    HAS_SNAPPY = False

# ─── A. Gemini 對話處理 ──────────────────────────────────────

def extract_gemini_title(content: str, filename: str) -> str:
    """從檔名或內容提取標題"""
    # 嘗試從檔名擷取（格式：中文標題_hash.md）
    stem = Path(filename).stem
    title = re.sub(r'_[0-9a-f]{8,}$', '', stem)
    if title and title != stem:
        return title.strip()
    # 從內容前三段提取
    lines = [l.strip() for l in content.split('\n') if l.strip() and not l.startswith('>') and not l.startswith('#')]
    if lines:
        return lines[0][:60]
    return stem

def extract_gemini_tags(content: str) -> list:
    """從內容推斷標籤"""
    keywords = {
        'AI': ['AI', 'GPT', 'LLM', '大模型', 'Gemini', 'Claude', '人工智慧'],
        '職涯': ['薪資', '職涯', '工作', '面試', '跳槽', '求職'],
        '技術': ['Python', 'Docker', 'SQL', 'API', 'Linux', '程式', 'code'],
        '生技': ['CartaBio', 'LaminDB', 'bioinfo', '生信', '細胞', '蛋白質', '基因'],
        '財務': ['薪資', 'ETF', '投資', '報稅', '資金'],
        '旅行': ['行程', '機票', '飛機', '日本', '韓國', '吉隆坡', '九州'],
        '生活': ['貓', '寵物', '飲食', '牙齒', '用藥'],
        '深圳': ['深圳', '中國', '大陸'],
        '台灣': ['台灣', '台北', '台中'],
    }
    tags = []
    for tag, words in keywords.items():
        if any(w.lower() in content.lower() for w in words):
            tags.append(tag)
    return tags[:6]

def parse_gemini_date(content: str, filename: str) -> str:
    """從備份時間或檔名推斷日期"""
    m = re.search(r'備份時間：\s*(\d{4}/\d{2}/\d{2})', content)
    if m:
        return m.group(1).replace('/', '-')
    return datetime.now().strftime('%Y-%m-%d')

def has_frontmatter(content: str) -> bool:
    return content.strip().startswith('---')

def add_frontmatter(content: str, filename: str) -> str:
    """為 Gemini 對話添加 YAML frontmatter"""
    if has_frontmatter(content):
        return content

    title   = extract_gemini_title(content, filename)
    tags    = extract_gemini_tags(content)
    date    = parse_gemini_date(content, filename)
    uid     = hashlib.md5(filename.encode()).hexdigest()[:12]
    summary = generate_summary(content, max_chars=150)

    front = f"""---
uuid: "{uid}"
title: "{title}"
date_created: {date}
date_updated: {date}
type: "chat"
source: "gemini"
tags: {json.dumps(tags, ensure_ascii=False)}
related_entities: []
summary: "{summary}"
---

"""
    return front + content

def generate_summary(content: str, max_chars: int = 150) -> str:
    """從內容生成簡短摘要（取前幾行有意義的文字）"""
    lines = [l.strip() for l in content.split('\n')
             if l.strip() and not l.startswith('#') and not l.startswith('>') and not l.startswith('---') and len(l.strip()) > 10]
    summary = ' '.join(lines[:3])[:max_chars]
    return summary.replace('"', "'").replace('\n', ' ')

def process_gemini_files(force: bool = False):
    """處理所有 Gemini 對話檔案，複製到 20_AI_Chats/Gemini 並添加 frontmatter"""
    if not GEMINI_SRC.exists():
        print(f"[SKIP] Gemini 來源目錄不存在: {GEMINI_SRC}")
        return

    processed = 0
    skipped   = 0

    for src_file in sorted(GEMINI_SRC.glob("*.md")):
        dst_file = GEMINI_DST / src_file.name
        if dst_file.exists() and not force:
            skipped += 1
            continue

        content = src_file.read_text(encoding='utf-8')
        new_content = add_frontmatter(content, src_file.name)
        dst_file.write_text(new_content, encoding='utf-8')
        processed += 1
        print(f"[GEMINI] {src_file.name}")

    print(f"[GEMINI] 處理完成：{processed} 個新增，{skipped} 個已存在")

# ─── B. Pages 手札處理 ───────────────────────────────────────

def extract_pages_text(path: Path) -> str:
    """從 .pages 檔案提取純文字（支援 snappy 壓縮）"""
    with zipfile.ZipFile(path) as z:
        with z.open('Index/Document.iwa') as f:
            data = f.read()

    if HAS_SNAPPY:
        try:
            data = snappy.decompress(data)
        except Exception:
            pass

    # 提取 CJK + ASCII 可讀字段
    chunks = re.findall(b'(?:[\xe4-\xe9][\x80-\xbf]{2}|[\xc0-\xdf][\x80-\xbf]|[\x20-\x7e])+', data)
    lines = []
    for c in chunks:
        s = c.decode('utf-8', errors='ignore').strip()
        # 過濾系統 metadata（純英數、格式字串等）
        if (len(s) > 3
                and any('\u4e00' <= ch <= '\u9fff' for ch in s)
                and not re.match(r'^[a-zA-Z0-9\s\-_\.#%,@]+$', s)):
            lines.append(s)

    # 去重並保留有意義的行
    seen = set()
    result = []
    for l in lines:
        if l not in seen and l not in ('y年M月d日', 'y/M/d'):
            seen.add(l)
            result.append(l)

    return '\n'.join(result)

def infer_journal_date(filename: str) -> tuple:
    """從檔名推斷日期，回傳 (date_str, year)"""
    stem = Path(filename).stem
    # 格式：YYMMDD 或 YYMMDD_subtitle
    m = re.match(r'^(\d{6})', stem)
    if m:
        yy, mm, dd = stem[0:2], stem[2:4], stem[4:6]
        year = int('20' + yy)
        return f'{year}-{mm}-{dd}', str(year)
    return datetime.now().strftime('%Y-%m-%d'), str(datetime.now().year)

def process_notes(force: bool = False):
    """將 .pages 手札轉換為 .md 並歸檔至 30_Journal"""
    if not NOTES_SRC.exists():
        print(f"[SKIP] Notes 來源目錄不存在: {NOTES_SRC}")
        return

    processed = 0
    skipped   = 0
    errors    = 0

    for src_file in sorted(NOTES_SRC.glob("*.pages")):
        date_str, year = infer_journal_date(src_file.name)
        out_dir  = JOURNAL_DST / year
        out_dir.mkdir(parents=True, exist_ok=True)
        stem     = re.sub(r'\.pages$', '', src_file.name)
        dst_file = out_dir / f"{stem}.md"

        if dst_file.exists() and not force:
            skipped += 1
            continue

        try:
            text = extract_pages_text(src_file)
        except Exception as e:
            print(f"[ERROR] {src_file.name}: {e}")
            errors += 1
            continue

        if not text.strip():
            print(f"[EMPTY] {src_file.name}")
            skipped += 1
            continue

        uid     = hashlib.md5(src_file.name.encode()).hexdigest()[:12]
        title   = f"手札 {date_str}"
        summary = generate_summary(text, max_chars=150)
        tags    = infer_journal_tags(text)

        md_content = f"""---
uuid: "{uid}"
title: "{title}"
date_created: {date_str}
date_updated: {date_str}
type: "note"
source: "pages"
tags: {json.dumps(tags, ensure_ascii=False)}
related_entities: []
summary: "{summary}"
---

{text}
"""
        dst_file.write_text(md_content, encoding='utf-8')
        processed += 1
        print(f"[NOTES] {src_file.name} → {dst_file.relative_to(BASE)}")

    print(f"[NOTES] 處理完成：{processed} 個新增，{skipped} 個已存在，{errors} 個錯誤")

def infer_journal_tags(text: str) -> list:
    keywords = {
        '深圳': ['深圳', '廣東', '南山'],
        '台灣': ['台灣', '台北', '台中', '松菸'],
        '職涯': ['工作', '離職', '跳槽', '公司', '職涯', '求職'],
        '感情': ['一樂', '感情', '遠距', '分離'],
        '寵物': ['奶茶', '芙蓉', '加菲', '貓'],
        '旅行': ['旅行', '機場', '飛機', '行程'],
        '自省': ['反思', '手札', '自省', '成長'],
    }
    tags = []
    for tag, words in keywords.items():
        if any(w in text for w in words):
            tags.append(tag)
    return tags[:5]

# ─── C. 索引生成器 ───────────────────────────────────────────

def extract_wikilinks(content: str) -> list:
    return re.findall(r'\[\[([^\]]+)\]\]', content)

def build_index(force: bool = False):
    """掃描所有 Markdown，生成 00_System/index.json"""
    index = {
        "generated_at": datetime.now().isoformat(),
        "total_files": 0,
        "files": {},
        "entity_map": {},
        "tag_map": {},
    }

    for md_file in sorted(BASE.rglob("*.md")):
        if '00_System' in str(md_file):
            continue

        content = md_file.read_text(encoding='utf-8')
        rel     = str(md_file.relative_to(BASE))

        # 解析 frontmatter
        fm = {}
        if content.startswith('---'):
            end = content.find('---', 3)
            if end > 0:
                fm_text = content[3:end]
                for line in fm_text.split('\n'):
                    if ':' in line:
                        k, _, v = line.partition(':')
                        fm[k.strip()] = v.strip().strip('"')

        wikilinks = extract_wikilinks(content)
        related   = fm.get('related_entities', '')

        entry = {
            "path":     rel,
            "title":    fm.get('title', md_file.stem),
            "type":     fm.get('type', 'note'),
            "date":     fm.get('date_created', ''),
            "tags":     fm.get('tags', '[]'),
            "summary":  fm.get('summary', ''),
            "wikilinks": wikilinks,
        }
        index["files"][rel] = entry
        index["total_files"] += 1

        # 建立 tag 索引
        for tag in re.findall(r'[\w\u4e00-\u9fff]+', fm.get('tags', '')):
            if tag not in ('true', 'false', 'null'):
                index["tag_map"].setdefault(tag, []).append(rel)

        # 建立 entity 索引
        for link in wikilinks:
            index["entity_map"].setdefault(link, []).append(rel)

    out_path = SYSTEM_DIR / "index.json"
    out_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[INDEX] 索引生成完成：{index['total_files']} 個檔案 → {out_path}")

# ─── 主程式 ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Personal Brain DB 處理工具')
    parser.add_argument('--all',     action='store_true', help='強制重新處理所有檔案')
    parser.add_argument('--reindex', action='store_true', help='只重新生成 index.json')
    args = parser.parse_args()

    if args.reindex:
        build_index()
        return

    force = args.all

    print("=== Personal Brain DB 處理開始 ===")
    print(f"模式: {'強制重處理' if force else '增量處理（跳過已存在）'}\n")

    process_gemini_files(force=force)
    print()
    process_notes(force=force)
    print()
    build_index()

    print("\n=== 處理完成 ===")

if __name__ == '__main__':
    main()
