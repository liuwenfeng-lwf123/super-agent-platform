"""Test ALL official models on Aliyun Bailian Coding Plan (Pro tier).

Per https://help.aliyun.com/zh/model-studio/coding-plan
Recommended: qwen3.6-plus (vision), kimi-k2.5 (vision), glm-5, MiniMax-M2.5
More:        qwen3.5-plus (vision), qwen3-max-2026-01-23, qwen3-coder-next,
             qwen3-coder-plus, glm-4.7
"""
import os
import sys
import time
from openai import OpenAI

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-sp-d56bcb39cd3c43abaf71c536f03df78d")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")

RECOMMENDED = [
    "qwen3.6-plus",
    "kimi-k2.5",
    "glm-5",
    "MiniMax-M2.5",
]
MORE = [
    "qwen3.5-plus",
    "qwen3-max-2026-01-23",
    "qwen3-coder-next",
    "qwen3-coder-plus",
    "glm-4.7",
]

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def try_model(model: str) -> dict:
    t = time.time()
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Answer in one short English sentence."},
                {"role": "user", "content": "What model are you? Reply briefly."},
            ],
            max_tokens=80,
            temperature=0.0,
        )
        dt = time.time() - t
        return {
            "ok": True,
            "latency": dt,
            "text": (r.choices[0].message.content or "").strip(),
            "usage": r.usage.model_dump() if r.usage else None,
        }
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        # keep only the short "message" part
        if "'message': '" in msg:
            try:
                msg = msg.split("'message': '", 1)[1].split("'", 1)[0]
            except Exception:  # noqa: BLE001
                pass
        return {"ok": False, "latency": time.time() - t, "text": f"{type(e).__name__}: {msg[:180]}"}


def main() -> int:
    print(f"Base URL: {BASE_URL}")
    print(f"API Key:  {API_KEY[:10]}...{API_KEY[-4:]}")
    print()

    all_models = [("RECOMMENDED", m) for m in RECOMMENDED] + [("MORE", m) for m in MORE]
    results = []
    for tier, m in all_models:
        r = try_model(m)
        tag = "OK" if r["ok"] else "NO"
        print(f"[{tag}] ({tier:11s}) {m:25s}  {r['latency']:5.2f}s  {r['text'][:120]}")
        results.append((tier, m, r))

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    ok_list = [(t, m, r) for t, m, r in results if r["ok"]]
    ng_list = [(t, m, r) for t, m, r in results if not r["ok"]]
    print(f"Working ({len(ok_list)}/{len(results)}):")
    for _, m, r in ok_list:
        print(f"  - {m:25s}  lat={r['latency']:.2f}s")
    if ng_list:
        print(f"\nFailed ({len(ng_list)}):")
        for _, m, r in ng_list:
            print(f"  - {m:25s}  {r['text'][:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
