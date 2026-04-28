"""Quick connectivity test for DashScope OpenAI-compatible endpoint.

Skipped under pytest by default. Run directly:
    RUN_DASHSCOPE=1 python -m pytest tests/test_dashscope.py
"""
import os
import sys
import pytest
from openai import OpenAI

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_DASHSCOPE"),
    reason="Live API test: set RUN_DASHSCOPE=1 to run",
)

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-sp-d56bcb39cd3c43abaf71c536f03df78d")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")

# Candidate models on DashScope coding endpoint (Qwen family)
CANDIDATE_MODELS = [
    "qwen3-coder-plus",
    "qwen3-coder-480b-a35b-instruct",
    "qwen-plus",
    "qwen-max",
]


def test_chat(model: str) -> tuple[bool, str]:
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a concise assistant."},
                {"role": "user", "content": "Reply with exactly: PONG"},
            ],
            max_tokens=16,
            temperature=0.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        return True, text
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def test_stream(model: str) -> tuple[bool, str]:
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Count: 1, 2, 3"}],
            max_tokens=32,
            stream=True,
        )
        chunks = []
        for ch in stream:
            delta = ch.choices[0].delta.content if ch.choices else None
            if delta:
                chunks.append(delta)
        return True, "".join(chunks).strip()
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    print(f"Base URL: {BASE_URL}")
    print(f"API Key:  {API_KEY[:10]}...{API_KEY[-4:]}")
    print("-" * 60)

    working_model = None
    for model in CANDIDATE_MODELS:
        print(f"\n[chat] model={model}")
        ok, out = test_chat(model)
        print(f"  ok={ok}  out={out!r}")
        if ok and not working_model:
            working_model = model

    if not working_model:
        print("\nNo model succeeded on chat.completions.")
        return 1

    print(f"\n[stream] model={working_model}")
    ok, out = test_stream(working_model)
    print(f"  ok={ok}  out={out!r}")

    print("\nDone. Working model:", working_model)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
