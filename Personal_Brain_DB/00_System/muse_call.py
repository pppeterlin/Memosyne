#!/usr/bin/env python3
"""
The Call of the Muses — 主動式記憶缺口提問
==========================================

繆思女神不只守護記憶，她們會主動開口：
當某位繆思的領域太安靜，她會呼喚你，問出那些記憶庫還不知道的事。

    "Clio is silent about your early years. Tell me of the days before 2022."

五種缺口來源（全部 deterministic，不需要 LLM、不需要向量索引）：

    silent_muse    某個領域資料夾全空（10_Profile / 30_Journal / ...）
    profile_topic  聖典（10_Profile）缺少標準主題：家庭、喜好、價值觀...
    thin_person    Tapestry 中被提及、但幾乎一無所知的人物
    temporal_gap   日記在某個月份一片寂靜
    stale_domain   領域太久沒有新記憶

流程：

    1. 掃描 Vault + Tapestry → 產生 gap 清單（每個 gap 有穩定 qid）
    2. 查 ledger（muse_call_ledger.jsonl）過濾冷卻中的問題
    3. 依優先度 + 當日種子洗牌 → 選出今日問題
    4. 互動問答；回答寫入 spring/ → 走標準 ingest 管線成為記憶

用法：

    python3 muse_call.py                  # 互動式每日儀式
    python3 muse_call.py --list           # 只列出今日問題（不記錄）
    python3 muse_call.py --list --json    # JSON 輸出（給 agent / cron）
    python3 muse_call.py --answer QID --text "..."   # 非互動回答（MCP 用）
    python3 muse_call.py --stats          # 提問 / 回答統計

環境變數：

    MEMOSYNE_LANG=en|zh          問題語言（預設 en）
    MEMOSYNE_CALL_COUNT=3        每日問題數
    MEMOSYNE_SPRING_DIR=...      spring/ 路徑覆寫（測試用）
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ─── 路徑解析 ─────────────────────────────────────────────────
# 與 artifacts.py 行為一致，但 env 在呼叫當下讀取（測試可重設）。

_SYSTEM_DIR = Path(__file__).resolve().parent
_BRAIN_DIR = _SYSTEM_DIR.parent
_REPO_ROOT = _BRAIN_DIR.parent

LEDGER_NAME = "muse_call_ledger.jsonl"


def vault_root() -> Path:
    """含九繆思領域資料夾（10_Profile ... 50_Knowledge）的資料根。"""
    env = os.getenv("MEMOSYNE_VAULT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    try:
        from artifacts import data_root
        return data_root()
    except ImportError:
        return _BRAIN_DIR


def ledger_path() -> Path:
    env = os.getenv("MEMOSYNE_ARTIFACT_DIR") or os.getenv("MEMOSYNE_VAULT_DIR")
    if env:
        return Path(env).expanduser() / LEDGER_NAME
    try:
        from artifacts import artifact_path
        return artifact_path("muse_call_ledger")
    except (ImportError, KeyError):
        return _SYSTEM_DIR / LEDGER_NAME


def spring_dir() -> Path:
    env = os.getenv("MEMOSYNE_SPRING_DIR")
    if env:
        return Path(env).expanduser()
    return _REPO_ROOT / "spring"


# ─── 冷卻策略 ─────────────────────────────────────────────────
# 同一個 qid 在冷卻期內不再被選中。

COOLDOWN_DAYS = {
    "answered": 120,   # 答過的問題很久不再問
    "skipped": 14,     # 跳過 = 現在不想答，兩週後再試
    "asked": 2,        # 問了沒答（中斷）→ 隔兩天再出現
}

DEFAULT_COUNT = 3
THIN_PERSON_MAX_EDGES = 2     # 邊數 ≤ 此值的人物視為「單薄」
TEMPORAL_LOOKBACK_MONTHS = 6  # 回看幾個月找日記空白
STALE_DAYS = 30               # 領域超過 N 天無新檔視為停滯

# ─── 問題庫 ───────────────────────────────────────────────────
# 每題：qid 穩定（kind:key），文案分 en / zh。
# muse 欄位只影響展示（哪位繆思開口），不影響選題。

_DOMAIN_DIRS = ["10_Profile", "30_Journal", "40_Projects", "50_Knowledge"]
# 20_AI_Chats 刻意排除：對話是被動累積的，不該叫使用者「補對話」。

_SILENT_MUSE_QUESTIONS = {
    "10_Profile": {
        "muse": "Polyhymnia",
        "en": "The Codex is nearly empty — tell me who you are: "
              "your name, where you live, and what you do.",
        "zh": "聖典幾乎是空白的——告訴我你是誰：名字、住在哪裡、在做什麼。",
    },
    "30_Journal": {
        "muse": "Thalia",
        "en": "No journal entries yet — what happened today, "
              "even the small things?",
        "zh": "日記還是空的——今天發生了什麼？再小的事都算。",
    },
    "40_Projects": {
        "muse": "Terpsichore",
        "en": "What projects or plans are you pursuing right now?",
        "zh": "你目前正在推進哪些專案或計畫？",
    },
    "50_Knowledge": {
        "muse": "Urania",
        "en": "What topic have you been learning or thinking about lately?",
        "zh": "你最近在鑽研或思考什麼主題？",
    },
}

# (topic_id, muse, en, zh, keywords) — keywords 命中 10_Profile 任一檔即視為已覆蓋
_PROFILE_TOPICS: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    ("origins", "Clio",
     "Where did you grow up, and what do you remember most about that place?",
     "你在哪裡長大？那個地方最讓你難忘的是什麼？",
     ("童年", "故鄉", "老家", "出生", "長大", "grew up", "childhood", "hometown")),
    ("family", "Polyhymnia",
     "Who is in your family? Tell me a little about each of them.",
     "你的家人有誰？簡單介紹一下他們吧。",
     ("家人", "家庭", "父親", "母親", "爸爸", "媽媽", "兄弟", "姊妹", "family", "parents")),
    ("education", "Urania",
     "What did you study, and how did you feel about your school years?",
     "你讀過什麼學校、學過什麼？那段日子過得如何？",
     ("大學", "學校", "科系", "主修", "畢業", "研究所", "education", "university", "degree")),
    ("work_history", "Terpsichore",
     "Walk me through the jobs you've had — which one shaped you most?",
     "聊聊你做過的工作——哪一份對你影響最深？",
     ("工作", "職涯", "公司", "職位", "career", "job", "work history")),
    ("food", "Thalia",
     "What foods do you love, and what would you never eat?",
     "你最愛吃什麼？又絕對不碰什麼？",
     ("愛吃", "美食", "料理", "口味", "餐廳", "food", "cuisine", "favorite dish")),
    ("music_art", "Euterpe",
     "What music, films, books, or art do you keep coming back to?",
     "有哪些音樂、電影、書或作品是你會一再回味的？",
     ("音樂", "電影", "樂團", "歌手", "藝術", "music", "movie", "film", "band")),
    ("hobbies", "Thalia",
     "How do you like to spend a completely free weekend?",
     "完全空閒的週末，你最想怎麼過？",
     ("興趣", "嗜好", "休閒", "hobby", "hobbies", "weekend")),
    ("values", "Polyhymnia",
     "What principles or values do you refuse to compromise on?",
     "有哪些原則或價值觀，是你絕不妥協的？",
     ("價值觀", "原則", "信念", "信仰", "values", "principle", "belief")),
    ("friends", "Erato",
     "Who are the friends that matter most to you right now?",
     "現在對你最重要的朋友是誰？說說他們吧。",
     ("朋友", "好友", "摯友", "friend", "friends")),
    ("health", "Polyhymnia",
     "What does taking care of yourself look like — sleep, exercise, habits?",
     "你平常怎麼照顧自己？睡眠、運動、習慣都算。",
     ("健康", "運動", "睡眠", "作息", "health", "exercise", "sleep")),
    ("goals", "Terpsichore",
     "What are you working toward in the next few years?",
     "接下來幾年，你最想完成什麼？",
     ("目標", "計畫", "夢想", "願望", "goal", "goals", "dream", "ambition")),
    ("dislikes", "Melpomene",
     "What things drain you or put you off instantly?",
     "有什麼事情會立刻消耗你、讓你避之唯恐不及？",
     ("討厭", "害怕", "恐懼", "受不了", "dislike", "hate", "fear")),
    ("routine", "Thalia",
     "Describe an ordinary day in your life, morning to night.",
     "描述你平凡的一天，從早到晚。",
     ("日常", "作息", "一天", "routine", "typical day")),
    ("places", "Clio",
     "Which places you've lived in or traveled to left a mark on you?",
     "哪些住過或旅行過的地方，在你身上留下了痕跡？",
     ("旅行", "旅遊", "住過", "搬家", "城市", "travel", "moved to", "lived in")),
]

_STALE_DOMAIN_QUESTIONS = {
    "30_Journal": {
        "muse": "Thalia",
        "en": "The journal has been quiet for a while — how have you been lately?",
        "zh": "日記安靜了好一陣子——最近過得好嗎？",
    },
    "40_Projects": {
        "muse": "Terpsichore",
        "en": "No project updates in a while — what are you building these days?",
        "zh": "專案領域好一陣子沒有新消息了——最近在做什麼？",
    },
    "50_Knowledge": {
        "muse": "Urania",
        "en": "Nothing new in Knowledge lately — learned anything interesting?",
        "zh": "知識庫最近沒有新東西——最近學到什麼有趣的嗎？",
    },
}

_PRIORITY = {
    "silent_muse": 0.95,
    "profile_topic": 0.85,
    "thin_person": 0.75,
    "temporal_gap": 0.65,
    "stale_domain": 0.55,
}

_MUSE_TITLES = {
    "Clio": "Keeper of History",
    "Thalia": "Voice of Daily Life",
    "Calliope": "Weaver of Conversations",
    "Urania": "Guardian of Wisdom",
    "Polyhymnia": "Singer of Identity",
    "Erato": "Muse of Love",
    "Melpomene": "Muse of Sorrow",
    "Terpsichore": "Muse of Action",
    "Euterpe": "Muse of Creation",
}


def _question(qid: str, kind: str, muse: str, en: str, zh: str,
              context: str = "") -> dict:
    return {
        "qid": qid, "kind": kind, "muse": muse,
        "priority": _PRIORITY[kind],
        "en": en, "zh": zh, "context": context,
    }


# ─── 缺口偵測 ─────────────────────────────────────────────────

def _md_files(root: Path) -> list[Path]:
    """領域資料夾下的記憶檔（略過 _ 開頭的系統目錄與 README）。"""
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*.md"):
        rel_parts = p.relative_to(root).parts
        if any(part.startswith(("_", ".")) for part in rel_parts):
            continue
        if p.name.lower() == "readme.md":
            continue
        out.append(p)
    return out


def find_silent_muses(vault: Path) -> list[dict]:
    gaps = []
    for domain in _DOMAIN_DIRS:
        if _md_files(vault / domain):
            continue
        spec = _SILENT_MUSE_QUESTIONS[domain]
        gaps.append(_question(
            f"silent_muse:{domain}", "silent_muse", spec["muse"],
            spec["en"], spec["zh"],
            context=f"domain {domain} has no memories yet",
        ))
    return gaps


def find_profile_topic_gaps(vault: Path) -> list[dict]:
    """聖典主題覆蓋檢查：10_Profile 全文 keyword 掃描。"""
    profile_files = _md_files(vault / "10_Profile")
    if not profile_files:
        return []   # 整個領域全空 → 交給 silent_muse，不重複出題
    corpus = ""
    for p in profile_files:
        try:
            corpus += p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
    gaps = []
    for topic_id, muse, en, zh, keywords in _PROFILE_TOPICS:
        if any(kw.lower() in corpus for kw in keywords):
            continue
        gaps.append(_question(
            f"profile_topic:{topic_id}", "profile_topic", muse, en, zh,
            context=f"10_Profile has no coverage of topic '{topic_id}'",
        ))
    return gaps


def find_thin_persons(limit: int = 5) -> list[dict]:
    """
    Tapestry 中被提及、但邊數 ≤ THIN_PERSON_MAX_EDGES 的人物。
    Kuzu / Tapestry 不可用時靜默回傳空（功能降級，不報錯）。
    """
    try:
        import tapestry
        conn = tapestry.get_conn()
        persons = tapestry.get_all_persons(conn)
    except Exception:
        return []
    gaps = []
    for person in persons:
        name = person.get("name", "") if isinstance(person, dict) else str(person)
        if not name:
            continue
        try:
            edges = tapestry._person_edge_count(conn, name)
        except Exception:
            continue
        if edges > THIN_PERSON_MAX_EDGES:
            continue
        gaps.append(_question(
            f"thin_person:{name}", "thin_person", "Erato",
            f"You've mentioned {name} — who are they to you, "
            f"and what should I remember about them?",
            f"你提過 {name}——這是誰？我該記住關於這個人的什麼？",
            context=f"person '{name}' has only {edges} edge(s) in the Tapestry",
        ))
        if len(gaps) >= limit:
            break
    return gaps


_DATE_PATTERNS = (
    re.compile(r"^(\d{2})(\d{2})(\d{2})"),            # 250604.md → 2025-06
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})"),          # 2025-06-04.md
)


def _journal_month(path: Path) -> str:
    """從日記檔名推月份 'YYYY-MM'；推不出來回傳空字串。"""
    stem = path.stem
    m = _DATE_PATTERNS[1].match(stem)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _DATE_PATTERNS[0].match(stem)
    if m:
        return f"20{m.group(1)}-{m.group(2)}"
    return ""


def find_temporal_gaps(vault: Path, today: datetime | None = None,
                       limit: int = 3) -> list[dict]:
    """近 N 個月（不含本月）中日記全空的月份。"""
    journal_files = _md_files(vault / "30_Journal")
    if not journal_files:
        return []   # 全空 → silent_muse 領域題已涵蓋
    covered = {_journal_month(p) for p in journal_files} - {""}
    now = today or datetime.now()
    gaps = []
    cursor = datetime(now.year, now.month, 1)
    for _ in range(TEMPORAL_LOOKBACK_MONTHS):
        cursor = (cursor - timedelta(days=1)).replace(day=1)  # 上個月 1 號
        month = f"{cursor.year:04d}-{cursor.month:02d}"
        if month in covered:
            continue
        gaps.append(_question(
            f"temporal_gap:{month}", "temporal_gap", "Clio",
            f"Your journal is silent for {month} — "
            f"what was happening in your life then?",
            f"{month} 的日記一片寂靜——那段時間你過得如何？",
            context=f"no journal entries found for {month}",
        ))
        if len(gaps) >= limit:
            break
    return gaps


def find_stale_domains(vault: Path, today: datetime | None = None) -> list[dict]:
    """有資料、但最新檔案超過 STALE_DAYS 天的領域。"""
    now = today or datetime.now()
    gaps = []
    for domain, spec in _STALE_DOMAIN_QUESTIONS.items():
        files = _md_files(vault / domain)
        if not files:
            continue
        try:
            newest = max(datetime.fromtimestamp(p.stat().st_mtime) for p in files)
        except OSError:
            continue
        idle_days = (now - newest).days
        if idle_days < STALE_DAYS:
            continue
        gaps.append(_question(
            f"stale_domain:{domain}", "stale_domain", spec["muse"],
            spec["en"], spec["zh"],
            context=f"{domain} has had no new file for {idle_days} days",
        ))
    return gaps


def find_all_gaps(today: datetime | None = None) -> list[dict]:
    vault = vault_root()
    gaps: list[dict] = []
    gaps += find_silent_muses(vault)
    gaps += find_profile_topic_gaps(vault)
    gaps += find_thin_persons()
    gaps += find_temporal_gaps(vault, today=today)
    gaps += find_stale_domains(vault, today=today)
    return gaps


# ─── Ledger ───────────────────────────────────────────────────

def _read_ledger() -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _append_ledger(entry: dict) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _latest_state(entries: list[dict]) -> dict[str, dict]:
    """每個 qid 的最新一筆紀錄（answered 永遠優先於同 qid 的後續 asked）。"""
    state: dict[str, dict] = {}
    for e in entries:
        qid = e.get("qid", "")
        if not qid:
            continue
        prev = state.get(qid)
        # answered 是終態：之後的 asked / skipped 不應該把冷卻變短
        if prev and prev.get("status") == "answered" and e.get("status") != "answered":
            continue
        state[qid] = e
    return state


def _in_cooldown(entry: dict, now: datetime) -> bool:
    status = entry.get("status", "")
    days = COOLDOWN_DAYS.get(status)
    if days is None:
        return False
    try:
        ts = datetime.fromisoformat(entry.get("ts", ""))
    except ValueError:
        return False
    return (now - ts) < timedelta(days=days)


def record(qid: str, kind: str, question: str, status: str,
           spring_file: str = "") -> None:
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "qid": qid, "kind": kind, "question": question, "status": status,
    }
    if spring_file:
        entry["spring_file"] = spring_file
    _append_ledger(entry)


# ─── 選題 ─────────────────────────────────────────────────────

def select_questions(count: int | None = None, lang: str = "",
                     today: datetime | None = None) -> list[dict]:
    """
    今日提問：缺口 → 冷卻過濾 → 優先度排序（同優先度依當日種子洗牌）。
    回傳的每題附 `question` 欄位（依 lang 解析後的文案）。
    """
    now = today or datetime.now()
    count = count or int(os.getenv("MEMOSYNE_CALL_COUNT", DEFAULT_COUNT))
    lang = (lang or os.getenv("MEMOSYNE_LANG", "en")).lower()
    if lang not in ("en", "zh"):
        lang = "en"

    state = _latest_state(_read_ledger())
    eligible = []
    for gap in find_all_gaps(today=now):
        prev = state.get(gap["qid"])
        if prev and _in_cooldown(prev, now):
            continue
        eligible.append(gap)

    rng = random.Random(now.strftime("%Y-%m-%d"))
    eligible.sort(key=lambda g: (-g["priority"], rng.random()))

    selected = eligible[:max(count, 0)]
    for q in selected:
        q["question"] = q[lang]
        q["muse_title"] = _MUSE_TITLES.get(q["muse"], "")
    return selected


# ─── 回答 → spring/ ──────────────────────────────────────────

def write_answers_to_spring(answers: list[tuple[dict, str]],
                            today: datetime | None = None) -> Path:
    """
    把 (question, answer) 寫入 spring/，讓標準 Spring Ritual 接手。
    同一天多次作答 append 到同一檔。
    """
    now = today or datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    sdir = spring_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    path = sdir / f"muse_call_{date_str}.md"

    if not path.exists():
        header = (
            "---\n"
            f'date: "{date_str}"\n'
            "type: journal\n"
            'source: "The Call of the Muses"\n'
            "---\n\n"
            "# The Call of the Muses\n"
        )
        path.write_text(header, encoding="utf-8")

    with path.open("a", encoding="utf-8") as f:
        for question, answer in answers:
            f.write(f"\n## {question['muse']} asks: {question['question']}\n\n")
            f.write(answer.rstrip() + "\n")
    return path


def submit_answer(qid: str, answer: str, lang: str = "") -> Path:
    """
    非互動回答（MCP / --answer 用）。
    qid 先從現存缺口找；找不到（已被回答覆蓋等）再從 ledger 撈最後一次提問文案。
    """
    answer = (answer or "").strip()
    if not answer:
        raise ValueError("answer is empty")

    question = None
    for gap in find_all_gaps():
        if gap["qid"] == qid:
            question = gap
            break
    if question is None:
        for e in reversed(_read_ledger()):
            if e.get("qid") == qid and e.get("question"):
                question = {
                    "qid": qid, "kind": e.get("kind", "unknown"),
                    "muse": "Mnemosyne", "question": e["question"],
                }
                break
    if question is None:
        raise KeyError(f"unknown question id: {qid}")

    if "question" not in question:
        lang = (lang or os.getenv("MEMOSYNE_LANG", "en")).lower()
        question["question"] = question.get(lang if lang in ("en", "zh") else "en",
                                            question.get("en", qid))

    path = write_answers_to_spring([(question, answer)])
    record(qid, question.get("kind", "unknown"), question["question"],
           "answered", spring_file=str(path))
    return path


# ─── 互動儀式 ─────────────────────────────────────────────────

def _read_multiline() -> str:
    """讀到空行為止；第一行就空 = 跳過。"""
    lines: list[str] = []
    while True:
        try:
            line = input("  > " if not lines else "    ")
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines).strip()


def run_ritual(count: int | None = None, lang: str = "",
               auto_ingest: bool = False) -> int:
    questions = select_questions(count=count, lang=lang)
    if not questions:
        print("🌊 The Muses are content. No questions today — "
              "the Vault wants for nothing.")
        return 0

    print("🎶 The Call of the Muses")
    print(f"   The Muses have {len(questions)} question(s) for you today.")
    print("   (Empty line to finish an answer; immediate empty line to skip.)\n")

    answered: list[tuple[dict, str]] = []
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] 🎭 {q['muse']} — {q['muse_title']}")
        print(f"      {q['question']}")
        try:
            answer = _read_multiline()
        except KeyboardInterrupt:
            print("\n   The rite is paused. Nothing is lost.")
            break
        if answer:
            answered.append((q, answer))
        else:
            record(q["qid"], q["kind"], q["question"], "skipped")
            print("      (skipped — the Muse will ask again another day)")
        print()

    if not answered:
        print("🌊 The waters are still. The Muses will call again.")
        return 0

    path = write_answers_to_spring(answered)
    for q, _ in answered:
        record(q["qid"], q["kind"], q["question"], "answered",
               spring_file=str(path))

    print(f"🌊 {len(answered)} memory fragment(s) await the Oracle:")
    print(f"   {path}")
    if auto_ingest:
        print("\n   Summoning the Spring Ritual...")
        import subprocess
        return subprocess.call([sys.executable, str(_SYSTEM_DIR / "ingest.py")],
                               cwd=str(_REPO_ROOT))
    print("   Run `memosyne ingest` to weave them into the Vault.")
    return 0


# ─── 統計 ─────────────────────────────────────────────────────

def stats() -> dict:
    entries = _read_ledger()
    counts = {"asked": 0, "answered": 0, "skipped": 0}
    for e in entries:
        s = e.get("status", "")
        if s in counts:
            counts[s] += 1
    by_kind: dict[str, int] = {}
    for e in entries:
        if e.get("status") == "answered":
            by_kind[e.get("kind", "unknown")] = by_kind.get(e.get("kind", "unknown"), 0) + 1
    open_gaps = find_all_gaps()
    return {
        "ledger": str(ledger_path()),
        "total_entries": len(entries),
        "counts": counts,
        "answered_by_kind": by_kind,
        "open_gaps": len(open_gaps),
        "open_gaps_by_kind": _count_by_kind(open_gaps),
    }


def _count_by_kind(gaps: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for g in gaps:
        out[g["kind"]] = out.get(g["kind"], 0) + 1
    return out


# ─── CLI ──────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="The Call of the Muses — 主動式記憶缺口提問")
    p.add_argument("--list", action="store_true",
                   help="只列出今日問題，不進入互動、不記錄")
    p.add_argument("--json", action="store_true",
                   help="搭配 --list / --stats：JSON 輸出（給 agent / cron）")
    p.add_argument("--count", type=int, default=0,
                   help=f"問題數（預設 env MEMOSYNE_CALL_COUNT 或 {DEFAULT_COUNT}）")
    p.add_argument("--lang", default="", choices=["", "en", "zh"],
                   help="問題語言（預設 env MEMOSYNE_LANG 或 en）")
    p.add_argument("--answer", default="", metavar="QID",
                   help="非互動回答指定問題（搭配 --text）")
    p.add_argument("--text", default="", help="--answer 的回答內容")
    p.add_argument("--stats", action="store_true", help="提問 / 回答統計")
    p.add_argument("--ingest", action="store_true",
                   help="互動結束後自動執行 Spring Ritual（ingest）")
    args = p.parse_args()

    if args.stats:
        data = stats()
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        print("🎶 The Call of the Muses — Chronicle")
        print(f"   ledger      : {data['ledger']}")
        c = data["counts"]
        print(f"   asked={c['asked']}  answered={c['answered']}  skipped={c['skipped']}")
        print(f"   open gaps   : {data['open_gaps']}")
        for kind, n in sorted(data["open_gaps_by_kind"].items()):
            print(f"     {kind:14s} {n}")
        return 0

    if args.answer:
        text = args.text
        if not text and not sys.stdin.isatty():
            text = sys.stdin.read()
        try:
            path = submit_answer(args.answer, text, lang=args.lang)
        except (KeyError, ValueError) as exc:
            print(f"The Oracle faltered: {exc}")
            return 1
        print(f"🌊 The answer has found its place: {path}")
        print("   Run `memosyne ingest` to weave it into the Vault.")
        return 0

    if args.list:
        questions = select_questions(count=args.count or None, lang=args.lang)
        if args.json:
            print(json.dumps(
                [{k: q[k] for k in ("qid", "kind", "muse", "question", "context")}
                 for q in questions],
                ensure_ascii=False, indent=2))
            return 0
        if not questions:
            print("🌊 The Muses are content. No questions today.")
            return 0
        print("🎶 The Call of the Muses — today's questions\n")
        for q in questions:
            print(f"  [{q['qid']}]")
            print(f"  🎭 {q['muse']}: {q['question']}\n")
        print("Answer interactively:  memosyne call")
        print('Answer one directly:   memosyne call --answer <QID> --text "..."')
        return 0

    return run_ritual(count=args.count or None, lang=args.lang,
                      auto_ingest=args.ingest)


if __name__ == "__main__":
    raise SystemExit(main())
