"""Full capability test for glm-5 on DashScope coding endpoint."""
import json
import os
import sys
import time
from openai import OpenAI

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-sp-d56bcb39cd3c43abaf71c536f03df78d")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")
MODEL = "glm-5"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def test_basic_chat() -> None:
    section(f"[1] basic chat  model={MODEL}")
    t = time.time()
    r = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a concise coding assistant."},
            {"role": "user", "content": "用一句话介绍你自己，并说明你是哪个模型。"},
        ],
        max_tokens=200,
        temperature=0.2,
    )
    dt = time.time() - t
    print(f"latency: {dt:.2f}s")
    print(f"content: {r.choices[0].message.content}")
    print(f"usage:   {r.usage}")


def test_stream() -> None:
    section(f"[2] streaming  model={MODEL}")
    t = time.time()
    first_token_t = None
    out = []
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "写一个 Python 函数: 计算斐波那契数列前 n 项。只输出代码，不要解释。"}],
        max_tokens=200,
        stream=True,
    )
    for ch in stream:
        if not ch.choices:
            continue
        delta = ch.choices[0].delta.content
        if delta:
            if first_token_t is None:
                first_token_t = time.time()
            out.append(delta)
    total = time.time() - t
    ttft = (first_token_t - t) if first_token_t else -1
    print(f"TTFT:   {ttft:.2f}s   total: {total:.2f}s   chars: {len(''.join(out))}")
    print("--- output ---")
    print("".join(out))


def test_tool_call() -> None:
    section(f"[3] tool calling  model={MODEL}")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                        "unit": {"type": "string", "enum": ["c", "f"]},
                    },
                    "required": ["city"],
                },
            },
        }
    ]
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "What's the weather in Tokyo right now? Use the tool."}],
        tools=tools,
        tool_choice="auto",
        max_tokens=200,
    )
    msg = r.choices[0].message
    print(f"finish_reason: {r.choices[0].finish_reason}")
    print(f"content:       {msg.content!r}")
    if msg.tool_calls:
        for tc in msg.tool_calls:
            print(f"tool_call:     name={tc.function.name}  args={tc.function.arguments}")
            try:
                args = json.loads(tc.function.arguments)
                print(f"parsed args:   {args}")
            except Exception as e:  # noqa: BLE001
                print(f"parse error:   {e}")
    else:
        print("tool_calls:    (none)")


def test_json_mode() -> None:
    section(f"[4] JSON mode  model={MODEL}")
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "Respond with pure JSON."},
                {"role": "user", "content": "Give me a JSON with keys name, age, city for a sample user."},
            ],
            response_format={"type": "json_object"},
            max_tokens=150,
        )
        text = r.choices[0].message.content
        print(f"raw:    {text}")
        try:
            obj = json.loads(text)
            print(f"parsed: {obj}")
        except Exception as e:  # noqa: BLE001
            print(f"JSON parse failed: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"response_format not supported: {type(e).__name__}: {e}")


if __name__ == "__main__":
    try:
        test_basic_chat()
        test_stream()
        test_tool_call()
        test_json_mode()
    except Exception as e:  # noqa: BLE001
        print(f"\nFATAL: {type(e).__name__}: {e}")
        sys.exit(1)
