"""Probe GLM availability on DashScope coding endpoint."""
import os
import sys
from openai import OpenAI

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-sp-d56bcb39cd3c43abaf71c536f03df78d")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")

# GLM candidates (ZhipuAI naming + possible DashScope aliases)
CANDIDATES = [
    "glm-4.6",
    "glm-4.5",
    "glm-4.5-air",
    "glm-4.5-flash",
    "glm-4-plus",
    "glm-4",
    "glm-4-air",
    "glm-4-flash",
    "glm-z1-air",
    "glm-z1-flash",
    "chatglm3-6b",
    "glm-4-coder",
    "codegeex-4",
]


def try_list_models(client: OpenAI) -> None:
    print("\n[GET /models]")
    try:
        res = client.models.list()
        ids = [m.id for m in res.data]
        print(f"  count={len(ids)}")
        for i in ids:
            print(f"   - {i}")
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {type(e).__name__}: {e}")


def try_chat(client: OpenAI, model: str) -> tuple[bool, str]:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply exactly: PONG"}],
            max_tokens=16,
            temperature=0.0,
        )
        return True, (r.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        # shorten very long errors
        if len(msg) > 200:
            msg = msg[:200] + "..."
        return False, f"{type(e).__name__}: {msg}"


def main() -> int:
    print(f"Base URL: {BASE_URL}")
    print(f"API Key:  {API_KEY[:10]}...{API_KEY[-4:]}")

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    try_list_models(client)

    print("\n[chat.completions probes]")
    ok_models = []
    for m in CANDIDATES:
        ok, out = try_chat(client, m)
        mark = "OK" if ok else "NO"
        print(f"  [{mark}] {m:30s} -> {out}")
        if ok:
            ok_models.append(m)

    print("\nWorking GLM-like models on this endpoint:", ok_models or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
