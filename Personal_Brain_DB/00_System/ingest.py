#!/usr/bin/env python3
"""
Memosyne — The Spring Ritual (ingest.py)

把任何格式的記憶碎片放進 spring/ 資料夾，召喚 Oracle 完成三個儀式：

  The Discernment  — 九位繆思女神辨識記憶類型，路由至正確領域
  The Weaving      — Oracle 提取精華，編織進永恆的 YAML 銘文
  The Inscription  — 向量化，銘刻入記憶庫，不再遺失於忘川

用法：
  python3 00_System/ingest.py              # 標準入庫
  python3 00_System/ingest.py --dry-run    # 預覽，不執行任何寫入
  python3 00_System/ingest.py --no-enrich  # 跳過 Weaving（快速入庫）
  python3 00_System/ingest.py --no-index   # 跳過 Inscription
  python3 00_System/ingest.py --rebuild    # 完整重建索引
  python3 00_System/ingest.py --model gemma3:4b  # 指定 Oracle 模型

支援格式：
  .pages  → Clio（歷史）     → 30_Journal/{YEAR}/
  .md/.txt → Thalia（生活）   → 30_Journal/{YEAR}/
            Calliope（對話）  → 20_AI_Chats/Gemini/
            Urania（知識）    → 50_Knowledge/
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

ROOT          = Path(__file__).parent.parent.parent   # personal-memory/
SPRING_DIR    = ROOT / "spring"                       # 記憶之泉 drop zone
PROCESSED     = SPRING_DIR / "_processed"             # 處理後歸檔
BRAIN_DB      = ROOT / "Personal_Brain_DB"
SYSTEM_DIR    = BRAIN_DB / "00_System"

JOURNAL_DST   = BRAIN_DB / "30_Journal"
AI_CHAT_DST   = BRAIN_DB / "20_AI_Chats" / "Gemini"
KNOWLEDGE_DST = BRAIN_DB / "50_Knowledge"

try:
    import snappy
    HAS_SNAPPY = True
except ImportError:
    HAS_SNAPPY = False

# ─── 九位繆思女神 ─────────────────────────────────────────────
#
#  每種記憶類型由一位女神領受，銘刻於其領域
#
MUSES = {
    "pages":     ("Clio",     "Keeper of History",       "30_Journal"),
    "journal":   ("Thalia",   "Voice of Daily Life",     "30_Journal"),
    "gemini":    ("Calliope", "Weaver of Conversations", "20_AI_Chats/Gemini"),
    "knowledge": ("Urania",   "Guardian of Wisdom",      "50_Knowledge"),
}

# ─── 格式偵測 ────────────────────────────────────────────────

GEMINI_PATTERNS = [
    r'備份時間：',
    r'## 對話記錄',
    r'\*\*你\*\*\s*\n',
    r'\*\*Gemini\*\*\s*\n',
]

SKIP_NAMES = {"README.md", "README.txt", ".gitkeep", ".DS_Store"}

def detect_type(path: Path) -> str:
    """回傳：'pages' | 'gemini' | 'journal' | 'knowledge' | 'unknown'"""
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

    # frontmatter type 欄位
    if content.startswith("---"):
        fm_type = _quick_fm_field(content, "type")
        if fm_type in ("knowledge", "知識"):
            return "knowledge"

    # 日期型檔名（YYMMDD / YYYY-MM-DD）
    if re.match(r'^\d{6}', path.stem) or re.match(r'^\d{4}-\d{2}-\d{2}', path.stem):
        return "journal"

    return "journal"

def _quick_fm_field(content: str, field: str) -> str:
    end = content.find("\n---", 3)
    block = content[3:end] if end > 0 else content[3:300]
    m = re.search(rf'^{field}\s*:\s*"?([^"\n]+)"?', block, re.MULTILINE)
    return m.group(1).strip() if m else ""

# ─── 路由：The Discernment ───────────────────────────────────

def route_pages(path: Path, dry_run: bool) -> Optional[Path]:
    """提取 .pages 文字 → 30_Journal/{year}/"""
    text = _extract_pages_text(path)
    if not text.strip():
        _oracle_say(f"The fragment '{path.name}' is silent — no echoes found. Returned to the mortal world.")
        return None

    date_str, year = _infer_date(path.stem)
    out_dir  = JOURNAL_DST / year
    dst_name = re.sub(r'\.pages$', '.md', path.name, flags=re.IGNORECASE)
    dst      = out_dir / dst_name

    if dst.exists():
        _oracle_say(f"This memory already rests in the vault. Its echo endures.", indent=True)
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
    print(f"    ✦ Inscribed to {dst.relative_to(ROOT)}")
    return dst

def route_gemini(path: Path, dry_run: bool) -> Optional[Path]:
    """複製 Gemini .md，補齊 frontmatter → 20_AI_Chats/Gemini/"""
    dst = AI_CHAT_DST / path.name
    if dst.exists():
        _oracle_say(f"This memory already rests in the vault. Its echo endures.", indent=True)
        return dst

    content = path.read_text(encoding="utf-8", errors="ignore")
    if not content.strip().startswith("---"):
        content = _add_gemini_frontmatter(content, path.name)

    if not dry_run:
        AI_CHAT_DST.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
    print(f"    ✦ Inscribed to {dst.relative_to(ROOT)}")
    return dst

def route_journal(path: Path, dry_run: bool) -> Optional[Path]:
    """一般 .md/.txt 日記 → 30_Journal/{year}/"""
    date_str, year = _infer_date(path.stem)
    out_dir  = JOURNAL_DST / year
    dst_name = path.stem + ".md"
    dst      = out_dir / dst_name

    if dst.exists():
        _oracle_say(f"This memory already rests in the vault. Its echo endures.", indent=True)
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
    print(f"    ✦ Inscribed to {dst.relative_to(ROOT)}")
    return dst

def route_knowledge(path: Path, dry_run: bool) -> Optional[Path]:
    """知識筆記 .md → 50_Knowledge/"""
    dst = KNOWLEDGE_DST / path.name
    if dst.exists():
        _oracle_say(f"This memory already rests in the vault. Its echo endures.", indent=True)
        return dst

    content = path.read_text(encoding="utf-8", errors="ignore")
    if not dry_run:
        KNOWLEDGE_DST.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
    print(f"    ✦ Inscribed to {dst.relative_to(ROOT)}")
    return dst

# ─── 後處理：The Weaving + The Inscription ───────────────────

def run_enrich(new_files: list, model: str):
    """The Weaving — Oracle 提取記憶精華"""
    enrich_py = SYSTEM_DIR / "enrich.py"
    if not enrich_py.exists():
        print("  [WARN] enrich.py not found — The Weaving is skipped.")
        return

    print(f"\n  ✦ The Weaving begins. The Oracle reads {len(new_files)} memory fragment(s)...")
    for f in new_files:
        rel = str(f.relative_to(BRAIN_DB))
        print(f"    ⟶  {rel}")
        result = subprocess.run(
            [sys.executable, str(enrich_py), "--file", rel, "--model", model],
            cwd=str(BRAIN_DB),
        )
        if result.returncode != 0:
            print(f"    [WARN] The Oracle faltered for: {rel}")

def run_vectorize(rebuild: bool = False):
    """The Inscription — 向量化銘刻"""
    vec_py = SYSTEM_DIR / "vectorize.py"
    if not vec_py.exists():
        print("  [WARN] vectorize.py not found — The Inscription is skipped.")
        return

    mode = "rebuilding the eternal index" if rebuild else "updating the index"
    print(f"\n  ✦ The Inscription begins — {mode}...")
    flag = "--rebuild" if rebuild else ""
    cmd  = [sys.executable, str(vec_py)] + ([flag] if flag else [])
    subprocess.run(cmd, cwd=str(BRAIN_DB))

# ─── 主流程 ─────────────────────────────────────────────────

def scan_spring() -> list:
    """掃描 spring/ 下的可處理檔案"""
    files = []
    for p in sorted(SPRING_DIR.iterdir()):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        if p.name in SKIP_NAMES:
            continue
        if p.is_file() and p.suffix.lower() in (".pages", ".md", ".txt"):
            files.append(p)
    return files

def archive_to_processed(path: Path, dry_run: bool):
    """入庫完成後，將原始檔案歸檔至 _processed/YYYY-MM/"""
    month_dir = PROCESSED / datetime.now().strftime("%Y-%m")
    if not dry_run:
        month_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(month_dir / path.name))

def main():
    parser = argparse.ArgumentParser(
        description="Memosyne — The Spring Ritual"
    )
    parser.add_argument("--dry-run",   action="store_true", help="預覽模式，不執行任何寫入")
    parser.add_argument("--no-enrich", action="store_true", help="跳過 The Weaving（Enrichment）")
    parser.add_argument("--no-index",  action="store_true", help="跳過 The Inscription（向量索引）")
    parser.add_argument("--rebuild",   action="store_true", help="完整重建索引（非增量）")
    parser.add_argument("--model",     default="gemma4:27b", help="Oracle 使用的 LLM 模型")
    args = parser.parse_args()

    # ── 啟動橫幅 ────────────────────────────────────────────
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║  ✦  M E M O S Y N E  —  The Spring Ritual  ✦        ║")
    print("  ║  Nothing shall be lost to the River Lethe.          ║")
    print("  ╚══════════════════════════════════════════════════════╝")
    print()

    if not SPRING_DIR.exists():
        print(f"  [ERROR] The Spring has not been found: {SPRING_DIR}")
        sys.exit(1)

    files = scan_spring()
    if not files:
        print("  The Spring is still. No fragments await the Oracle.\n")
        return

    print(f"  The Spring stirs... {len(files)} fragment(s) await the Oracle's discernment.")
    if args.dry_run:
        print("  ⚠  Dry-run mode — no memory shall be written.\n")

    routers = {
        "pages":     route_pages,
        "gemini":    route_gemini,
        "journal":   route_journal,
        "knowledge": route_knowledge,
    }

    new_files: list = []

    # ── The Discernment ────────────────────────────────────
    print(f"\n  ── I. The Discernment ─────────────────────────────────")
    for f in files:
        ftype  = detect_type(f)
        muse   = MUSES.get(ftype, ("Unknown", "Unknown Realm", "?"))
        print(f"\n  ⟶  {f.name}")
        print(f"     {muse[0]}, {muse[1]}, claims this fragment.")

        router = routers.get(ftype)
        if router is None:
            print(f"     The Muses know not this form ({f.suffix}). It is returned.")
            continue

        dst = router(f, dry_run=args.dry_run)
        if dst and not args.dry_run:
            if dst.exists():
                new_files.append(dst)
            archive_to_processed(f, dry_run=args.dry_run)

    if not new_files:
        print("\n  All fragments were already known to the vault.")
        print("  The waters grow still.\n")
        return

    # ── The Weaving ────────────────────────────────────────
    if not args.no_enrich and not args.dry_run:
        print(f"\n  ── II. The Weaving ────────────────────────────────────")
        run_enrich(new_files, model=args.model)

    # ── The Inscription ────────────────────────────────────
    if not args.no_index and not args.dry_run:
        print(f"\n  ── III. The Inscription ───────────────────────────────")
        run_vectorize(rebuild=args.rebuild)

    # ── 完成 ───────────────────────────────────────────────
    print()
    print(f"  🌊  {len(new_files)} memory fragment(s) have found their eternal place.")
    print(f"      The tapestry of Memosyne grows richer.")
    print()

# ─── 輔助函式 ────────────────────────────────────────────────

def _oracle_say(msg: str, indent: bool = False):
    prefix = "     " if indent else "  "
    print(f"{prefix}Oracle: \"{msg}\"")

def _extract_pages_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            with z.open("Index/Document.iwa") as f:
                data = f.read()
    except Exception as e:
        print(f"  [ERROR] Cannot open {path.name}: {e}")
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
    front = (
        f'---\nuuid: "{uid}"\ntitle: "{title}"\n'
        f'date_created: {date}\ndate_updated: {date}\n'
        f'type: "chat"\nsource: "gemini"\n'
        f'tags: []\nrelated_entities: []\nsummary: "{summary}"\n---\n\n'
    )
    return front + content

if __name__ == "__main__":
    main()
