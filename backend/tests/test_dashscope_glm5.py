"""Probe GLM-5 (and other third-party) availability on DashScope coding endpoint."""
import os
import sys
from openai import OpenAI

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-sp-d56bcb39cd3c43abaf71c536f03df78d")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")

CANDIDATES = [
    # GLM-5 / GLM-4.6 variants
    "glm-5",
    "glm-5-air",
    "glm-5-flash",
    "glm5",
    "GLM-5",
    "zhipu/glm-5",
    "zhipuai/glm-5",
    "zai/glm-5",
    "zai/glm-4.6",
    "z-ai/glm-4.6",
    "z-ai/glm-4.5",
    "ZhipuAI/glm-4.5",
    "ZhipuAI/GLM-4.5",
    "ZhipuAI/glm-4.6",
    # Other popular models this endpoint may proxy
    "kimi-k2",
    "kimi-k2-0711-preview",
    "moonshotai/Kimi-K2-Instruct",
    "deepseek-v3",
    "deepseek-v3.1",
    "deepseek-chat",
    "deepseek-coder",
    "claude-sonnet-4",
    "claude-3-5-sonnet",
    # qwen full list
    "qwen3-coder-plus",
    "qwen3-coder-flash",
    "qwen3-coder",
    "qwen-coder-turbo",
    "qwen3-max",
    "qwen3-235b-a22b-instruct",
    "qwen-plus-latest",
]


def try_chat(client: OpenAI, model: str) -> tuple[bool, str]:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply: PONG"}],
            max_tokens=16,
            temperature=0.0,
        )
        return True, (r.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        # only keep the short error code/message
        if "'message': '" in msg:
            try:
                m = msg.split("'message': '", 1)[1].split("'", 1)[0]
                return False, m
            except Exception:  # noqa: BLE001
                pass
        if len(msg) > 160:
            msg = msg[:160] + "..."
        return False, f"{type(e).__name__}: {msg}"


def main() -> int:
    print(f"Base URL: {BASE_URL}")
    print(f"API Key:  {API_KEY[:10]}...{API_KEY[-4:]}\n")

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    ok = []
    for m in CANDIDATES:
        passed, out = try_chat(client, m)
        mark = "OK" if passed else "no"
        print(f"  [{mark}] {m:35s} -> {out}")
        if passed:
            ok.append((m, out))

    print("\nWorking models on this endpoint:")
    for m, o in ok:
        print(f"  * {m}  ({o!r})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
