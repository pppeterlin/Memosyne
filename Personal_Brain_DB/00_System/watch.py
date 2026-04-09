#!/usr/bin/env python3
"""
Personal Brain DB — 檔案監控守護程式
監控 gemini chat 和 notes 目錄，有新檔案進入時自動處理

安裝依賴：pip install watchdog
執行：python3 watch.py
"""

import time
import subprocess
import sys
from pathlib import Path

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("請先安裝 watchdog：pip install watchdog")
    sys.exit(1)

BASE     = Path(__file__).parent.parent
GEMINI_SRC = BASE.parent / "gemini chat"
NOTES_SRC  = BASE.parent / "notes"
SCRIPT     = Path(__file__).parent / "process_files.py"


class NewFileHandler(FileSystemEventHandler):
    def __init__(self, label: str):
        self.label = label
        self._debounce = {}

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix not in ('.md', '.pages'):
            return

        # 簡單 debounce（同一檔案 2 秒內只處理一次）
        now = time.time()
        if self._debounce.get(str(path), 0) + 2 > now:
            return
        self._debounce[str(path)] = now

        print(f"[WATCH][{self.label}] 偵測到新檔案：{path.name}")
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            print(f"[ERROR] {result.stderr.strip()}")


def main():
    observer = Observer()

    if GEMINI_SRC.exists():
        observer.schedule(NewFileHandler("Gemini"), str(GEMINI_SRC), recursive=False)
        print(f"[WATCH] 監控 Gemini 目錄：{GEMINI_SRC}")
    else:
        print(f"[SKIP] Gemini 目錄不存在：{GEMINI_SRC}")

    if NOTES_SRC.exists():
        observer.schedule(NewFileHandler("Notes"), str(NOTES_SRC), recursive=False)
        print(f"[WATCH] 監控 Notes 目錄：{NOTES_SRC}")
    else:
        print(f"[SKIP] Notes 目錄不存在：{NOTES_SRC}")

    observer.start()
    print("[WATCH] 守護程式啟動，按 Ctrl+C 停止\n")

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
        print("\n[WATCH] 已停止")

    observer.join()


if __name__ == '__main__':
    main()
