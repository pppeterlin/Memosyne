#!/usr/bin/env python3
"""MiMo 端點穩定性壓測 — 連打 N 次，回報成功率 / 延遲 / 錯誤型別。

用法：
    .venv/bin/python Personal_Brain_DB/00_System/probe_mimo.py            # 預設 20 次小 payload
    .venv/bin/python Personal_Brain_DB/00_System/probe_mimo.py --n 30 --big   # 模擬 enrich 的 ~3000 字 payload
"""
import argparse
import os
import time
from collections import Counter

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

BASE_URL = os.environ.get("PROXY_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
API_KEY  = os.environ.get("PROXY_API_KEY", "")
MODEL    = "mimo-v2.5-pro"

SMALL_PROMPT = "只回覆一個字：OK"
# 模擬 enrich 真實負載：~3000 字中文 + 要求回 JSON
BIG_PROMPT = ("以下是一段個人筆記，請用 JSON 回覆 {\"ok\": true}：\n" + "這是一段測試文字。" * 300)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="連打次數")
    ap.add_argument("--big", action="store_true", help="用 ~3000 字 payload（貼近 enrich 實況）")
    ap.add_argument("--keepalive", action="store_true", help="開啟 keep-alive（預設關閉，比照 enrich）")
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()

    if not API_KEY:
        print("❌ PROXY_API_KEY 未設定（.env）"); return

    prompt = BIG_PROMPT if args.big else SMALL_PROMPT
    print(f"端點: {BASE_URL}")
    print(f"模型: {MODEL} | 次數: {args.n} | payload: {'~3000字' if args.big else 'small'} "
          f"| keep-alive: {args.keepalive} | timeout: {args.timeout}s")
    print("-" * 64)

    import httpx
    http_client = None if args.keepalive else httpx.Client(
        limits=httpx.Limits(max_keepalive_connections=0))
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=args.timeout,
                    max_retries=0, http_client=http_client)

    ok = 0
    latencies = []
    errors = Counter()
    for i in range(1, args.n + 1):
        t0 = time.time()
        try:
            r = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0, max_completion_tokens=32,
            )
            dt = time.time() - t0
            latencies.append(dt)
            ok += 1
            txt = (r.choices[0].message.content or "").strip().replace("\n", " ")[:20]
            print(f"  [{i:2}] ✅ {dt:5.1f}s  {txt!r}")
        except Exception as e:
            dt = time.time() - t0
            name = type(e).__name__
            errors[name] += 1
            print(f"  [{i:2}] ❌ {dt:5.1f}s  {name}: {str(e)[:80]}")

    print("-" * 64)
    rate = ok / args.n * 100
    print(f"成功率: {ok}/{args.n} = {rate:.0f}%")
    if latencies:
        latencies.sort()
        avg = sum(latencies) / len(latencies)
        p50 = latencies[len(latencies) // 2]
        print(f"延遲(成功): avg {avg:.1f}s  min {latencies[0]:.1f}s  "
              f"p50 {p50:.1f}s  max {latencies[-1]:.1f}s")
    if errors:
        print("錯誤分布: " + ", ".join(f"{k}×{v}" for k, v in errors.most_common()))


if __name__ == "__main__":
    main()
