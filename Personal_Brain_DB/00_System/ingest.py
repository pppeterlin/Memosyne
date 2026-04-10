#!/usr/bin/env python3
"""
Mnemosyne Ingestion Pipeline — ingest.py

Mneme 是記憶入口（靈感來自記憶女神 Mnemosyne）。
把任何格式的檔案放進 Mneme/ 資料夾，本腳本自動完成：
  格式偵測 → 歸檔至正確目錄 → Enrichment → 向量索引

用法：
  python3 00_System/ingest.py              # 處理 Mneme/ 下所有新檔案
  python3 00_System/ingest.py --dry-run    # 預覽，不執行任何寫入
  python3 00_System/ingest.py --no-enrich  # 跳過 Enrichment（快速入庫）
  python3 00_System/ingest.py --no-index   # 跳過向量索引重建
  python3 00_System/ingest.py --all        # 強制重新處理（含已處理過的）

支援格式：
  .pages   → 30_Journal/{YEAR}/
  .md      → Gemini 匯出  → 20_AI_Chats/Gemini/
             一般日記      → 30_Journal/{YEAR}/
             知識筆記      → 50_Knowledge/
  .txt     → 30_Journal/{YEAR}/（自動轉 .md）
"""

import os
import re
import sys
import json
import shutil
import hashlib
import zipfile
import argparse
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime

# ─── 路徑設定 ────────────────────────────────────────────────

ROOT         = Path(__file__).parent.parent.parent   # personal-memory/
MNEME_DIR    = ROOT / "Mneme"                        # 記憶入口 drop zone
PROCESSED    = MNEME_DIR / "_processed"              # 處理後歸檔
BRAIN_DB     = ROOT / "Personal_Brain_DB"
SYSTEM_DIR   = BRAIN_DB / "00_System"

JOURNAL_DST  = BRAIN_DB / "30_Journal"
AI_CHAT_DST  = BRAIN_DB / "20_AI_Chats" / "Gemini"
KNOWLEDGE_DST = BRAIN_DB / "50_Knowledge"

try:
    import snappy
    HAS_SNAPPY = True
except ImportError:
    HAS_SNAPPY = False

# ─── 格式偵測 ────────────────────────────────────────────────

GEMINI_PATTERNS = [
    r'備份時間：',
    r'## 對話記錄',
    r'\*\*你\*\*\s*\n',
    r'\*\*Gemini\*\*\s*\n',
]

def detect_type(path: Path) -> str:
    """
    回傳：'pages' | 'gemini' | 'journal' | 'knowledge' | 'unknown'
    """
    if path.suffix.lower() == ".pages":
        return "pages"

    if path.suffix.lower() not in (".md", ".txt"):
        return "unknown"

    content = path.read_text(encoding="utf-8", errors="ignore")

    # Gemini 匯出：特徵字串 or 帶 hash 的檔名
    if any(re.search(p, content) for p in GEMINI_PATTERNS):
        return "gemini"
    if re.search(r'_[0-9a-f]{8,16}\.md$', path.name):
        return "gemini"

    # 讀 frontmatter 的 type 欄位
    if content.startswith("---"):
        fm_type = _quick_fm_field(content, "type")
        if fm_type in ("knowledge", "知識"):
            return "knowledge"

    # 檔名是日期格式（YYMMDD / YYYY-MM-DD）
    if re.match(r'^\d{6}', path.stem) or re.match(r'^\d{4}-\d{2}-\d{2}', path.stem):
        return "journal"

    return "journal"  # 預設歸入日記

def _quick_fm_field(content: str, field: str) -> str:
    end = content.find("\n---", 3)
    block = content[3:end] if end > 0 else content[3:300]
    m = re.search(rf'^{field}\s*:\s*"?([^"\n]+)"?', block, re.MULTILINE)
    return m.group(1).strip() if m else ""

# ─── 路由：決定目標路徑 ───────────────────────────────────────

def route_pages(path: Path, dry_run: bool) -> Optional[Path]:
    """提取 .pages 文字，轉存為 .md → 30_Journal/{year}/"""
    text = _extract_pages_text(path)
    if not text.strip():
        print(f"  [EMPTY]  {path.name} — 無可提取內容，跳過")
        return None

    date_str, year = _infer_date(path.stem)
    out_dir  = JOURNAL_DST / year
    dst_name = re.sub(r'\.pages$', '.md', path.name, flags=re.IGNORECASE)
    dst      = out_dir / dst_name

    if dst.exists():
        print(f"  [EXISTS] {path.name} → {dst.relative_to(ROOT)} (已存在，跳過)")
        return dst

    uid     = hashlib.md5(path.name.encode()).hexdigest()[:12]
    summary = _summary(text)
    tags    = _journal_tags(text)
    md = (
        f'---\nuuid: "{uid}"\ntitle: "手札 {date_str}"\n'
        f'date_created: {date_str}\ndate_updated: {date_str}\n'
        f'type: "note"\nsource: "pages"\n'
        f'tags: {json.dumps(tags, ensure_ascii=False)}\n'
        f'related_entities: []\nsummary: "{summary}"\n---\n\n{text}\n'
    )
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        dst.write_text(md, encoding="utf-8")
    print(f"  [PAGES]  {path.name} → {dst.relative_to(ROOT)}")
    return dst

def route_gemini(path: Path, dry_run: bool) -> Optional[Path]:
    """複製 Gemini .md，補齊 frontmatter → 20_AI_Chats/Gemini/"""
    dst = AI_CHAT_DST / path.name
    if dst.exists():
        print(f"  [EXISTS] {path.name} → {dst.relative_to(ROOT)} (已存在，跳過)")
        return dst

    content = path.read_text(encoding="utf-8", errors="ignore")
    if not content.strip().startswith("---"):
        content = _add_gemini_frontmatter(content, path.name)

    if not dry_run:
        AI_CHAT_DST.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
    print(f"  [GEMINI] {path.name} → {dst.relative_to(ROOT)}")
    return dst

def route_journal(path: Path, dry_run: bool) -> Optional[Path]:
    """一般 .md/.txt 日記 → 30_Journal/{year}/"""
    date_str, year = _infer_date(path.stem)
    out_dir  = JOURNAL_DST / year
    dst_name = path.stem + ".md"
    dst      = out_dir / dst_name

    if dst.exists():
        print(f"  [EXISTS] {path.name} → {dst.relative_to(ROOT)} (已存在，跳過)")
        return dst

    content = path.read_text(encoding="utf-8", errors="ignore")
    if not content.strip().startswith("---"):
        uid     = hashlib.md5(path.name.encode()).hexdigest()[:12]
        summary = _summary(content)
        tags    = _journal_tags(content)
        front   = (
            f'---\nuuid: "{uid}"\ntitle: "手札 {date_str}"\n'
            f'date_created: {date_str}\ndate_updated: {date_str}\n'
            f'type: "note"\nsource: "manual"\n'
            f'tags: {json.dumps(tags, ensure_ascii=False)}\n'
            f'related_entities: []\nsummary: "{summary}"\n---\n\n'
        )
        content = front + content

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
    print(f"  [NOTE]   {path.name} → {dst.relative_to(ROOT)}")
    return dst

def route_knowledge(path: Path, dry_run: bool) -> Optional[Path]:
    """知識筆記 .md → 50_Knowledge/"""
    dst = KNOWLEDGE_DST / path.name
    if dst.exists():
        print(f"  [EXISTS] {path.name} → {dst.relative_to(ROOT)} (已存在，跳過)")
        return dst

    content = path.read_text(encoding="utf-8", errors="ignore")
    if not dry_run:
        KNOWLEDGE_DST.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
    print(f"  [KNOWL]  {path.name} → {dst.relative_to(ROOT)}")
    return dst

# ─── 後處理：Enrichment + Vectorize ──────────────────────────

def run_enrich(new_files: list, model: str = "gemma4:27b"):
    """對新入庫的檔案執行 Enrichment"""
    enrich_py = SYSTEM_DIR / "enrich.py"
    if not enrich_py.exists():
        print("[WARN] enrich.py 不存在，跳過 Enrichment")
        return

    print(f"\n[ENRICH] 對 {len(new_files)} 個新檔案執行 Enrichment...")
    for f in new_files:
        rel = str(f.relative_to(BRAIN_DB))
        result = subprocess.run(
            [sys.executable, str(enrich_py), "--file", rel, "--model", model],
            cwd=str(BRAIN_DB),
        )
        if result.returncode != 0:
            print(f"  [WARN] Enrichment 失敗: {rel}")

def run_vectorize(rebuild: bool = False):
    """重建向量索引"""
    vec_py = SYSTEM_DIR / "vectorize.py"
    if not vec_py.exists():
        print("[WARN] vectorize.py 不存在，跳過索引")
        return

    flag = "--rebuild" if rebuild else ""
    cmd  = [sys.executable, str(vec_py)] + ([flag] if flag else [])
    print(f"\n[INDEX]  {'重建' if rebuild else '增量更新'}向量索引...")
    subprocess.run(cmd, cwd=str(BRAIN_DB))

# ─── 主流程 ─────────────────────────────────────────────────

SKIP_NAMES = {"README.md", "README.txt", ".gitkeep", ".DS_Store"}

def scan_mneme() -> list:
    """掃描 Mneme/ 下的可處理檔案（排除 _* 子目錄和說明文件）"""
    files = []
    for p in sorted(MNEME_DIR.iterdir()):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        if p.name in SKIP_NAMES:
            continue
        if p.is_file() and p.suffix.lower() in (".pages", ".md", ".txt"):
            files.append(p)
    return files

def archive_to_processed(path: Path, dry_run: bool):
    """把 Mneme/ 中的原始檔案搬到 _processed/YYYY-MM/"""
    month_dir = PROCESSED / datetime.now().strftime("%Y-%m")
    if not dry_run:
        month_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(month_dir / path.name))
    print(f"  [ARCHIVE] {path.name} → _processed/{month_dir.name}/")

def main():
    parser = argparse.ArgumentParser(description="Mnemosyne 記憶入庫流程")
    parser.add_argument("--dry-run",    action="store_true", help="預覽模式，不執行任何寫入")
    parser.add_argument("--no-enrich",  action="store_true", help="跳過 Enrichment")
    parser.add_argument("--no-index",   action="store_true", help="跳過向量索引重建")
    parser.add_argument("--rebuild",    action="store_true", help="重建向量索引（非增量）")
    parser.add_argument("--model",      default="gemma4:27b", help="Enrichment 使用的 LLM 模型")
    parser.add_argument("--all",        action="store_true", help="強制重新處理（含已存在的目標）")
    args = parser.parse_args()

    if not MNEME_DIR.exists():
        print(f"[ERROR] Mneme/ 資料夾不存在: {MNEME_DIR}")
        sys.exit(1)

    files = scan_mneme()
    if not files:
        print("✨ Mneme/ 目前為空，沒有新檔案需要入庫。")
        return

    print(f"=== Mnemosyne 記憶入庫 ({len(files)} 個檔案) ===")
    if args.dry_run:
        print("⚠️  預覽模式（dry-run）— 不執行任何寫入\n")

    routers = {
        "pages":     route_pages,
        "gemini":    route_gemini,
        "journal":   route_journal,
        "knowledge": route_knowledge,
    }

    new_files: list = []

    for f in files:
        ftype = detect_type(f)
        print(f"\n→ {f.name}  [{ftype}]")
        router = routers.get(ftype)
        if router is None:
            print(f"  [SKIP] 不支援的格式: {f.suffix}")
            continue

        dst = router(f, dry_run=args.dry_run)
        if dst and not args.dry_run:
            # 只記錄「實際新增」的目標（非 EXISTS）
            rel_dst = str(dst.relative_to(BRAIN_DB))
            if dst.exists():
                new_files.append(dst)
            archive_to_processed(f, dry_run=args.dry_run)

    if not new_files:
        print("\n✅ 沒有新增的檔案，流程結束。")
        return

    print(f"\n共新增 {len(new_files)} 個記憶檔案。")

    # Step 2: Enrichment
    if not args.no_enrich and not args.dry_run:
        run_enrich(new_files, model=args.model)

    # Step 3: Vectorize
    if not args.no_index and not args.dry_run:
        run_vectorize(rebuild=args.rebuild)

    print("\n🧠 入庫完成！")

# ─── 輔助函式 ────────────────────────────────────────────────

def _extract_pages_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            with z.open("Index/Document.iwa") as f:
                data = f.read()
    except Exception as e:
        print(f"  [ERROR] 無法讀取 {path.name}: {e}")
        return ""

    if HAS_SNAPPY:
        try:
            data = __import__("snappy").decompress(data)
        except Exception:
            pass

    chunks = re.findall(b'(?:[\xe4-\xe9][\x80-\xbf]{2}|[\xc0-\xdf][\x80-\xbf]|[\x20-\x7e])+', data)
    seen, result = set(), []
    for c in chunks:
        s = c.decode("utf-8", errors="ignore").strip()
        if (len(s) > 3
                and any('\u4e00' <= ch <= '\u9fff' for ch in s)
                and not re.match(r'^[a-zA-Z0-9\s\-_\.#%,@]+$', s)
                and s not in seen
                and s not in ('y年M月d日', 'y/M/d')):
            seen.add(s)
            result.append(s)
    return '\n'.join(result)

def _infer_date(stem: str) -> tuple:
    m = re.match(r'^(\d{6})', stem)
    if m:
        yy, mm, dd = stem[0:2], stem[2:4], stem[4:6]
        year = int('20' + yy)
        return f'{year}-{mm}-{dd}', str(year)
    m2 = re.match(r'^(\d{4})-(\d{2})-(\d{2})', stem)
    if m2:
        return m2.group(0), m2.group(1)
    now = datetime.now()
    return now.strftime('%Y-%m-%d'), str(now.year)

def _summary(text: str, max_chars: int = 150) -> str:
    lines = [l.strip() for l in text.split('\n')
             if l.strip() and not l.startswith('#') and not l.startswith('>') and len(l.strip()) > 10]
    return ' '.join(lines[:3])[:max_chars].replace('"', "'").replace('\n', ' ')

def _journal_tags(text: str) -> list:
    keywords = {
        'Osaka':  ['Osaka', '廣東', '南山'],
        '台灣':  ['台灣', '台北', '台中'],
        '職涯':  ['工作', '離職', '跳槽', '公司', '求職'],
        '感情':  ['一樂', '感情', '遠距'],
        '寵物':  ['奶茶', '芙蓉', '加菲', '貓'],
        '旅行':  ['旅行', '機場', '飛機', '行程'],
        '自省':  ['反思', '手札', '自省', '成長'],
            '雲南':  ['CityA', 'LakeB', '雲南'],
    }
    return [tag for tag, words in keywords.items() if any(w in text for w in words)][:5]

def _add_gemini_frontmatter(content: str, filename: str) -> str:
    stem = Path(filename).stem
    title = re.sub(r'_[0-9a-f]{8,}$', '', stem).strip() or stem
    m = re.search(r'備份時間：\s*(\d{4}/\d{2}/\d{2})', content)
    date = m.group(1).replace('/', '-') if m else datetime.now().strftime('%Y-%m-%d')
    uid = hashlib.md5(filename.encode()).hexdigest()[:12]
    summary = _summary(content)
    tags: list = []
    front = (
        f'---\nuuid: "{uid}"\ntitle: "{title}"\n'
        f'date_created: {date}\ndate_updated: {date}\n'
        f'type: "chat"\nsource: "gemini"\n'
        f'tags: {json.dumps(tags, ensure_ascii=False)}\n'
        f'related_entities: []\nsummary: "{summary}"\n---\n\n'
    )
    return front + content

if __name__ == "__main__":
    main()
