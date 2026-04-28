"""End-to-end test: use the project's own ChatOpenAI wrapper path against glm-5.

This simulates what backend/app/models/provider.py does when a user adds a custom
provider through the UI: pass model / api_key / base_url to LangChain's ChatOpenAI,
then run streaming + tool calling.

This file hits a live API and is skipped under pytest by default. Run directly:
    python tests/test_glm5_e2e.py
"""
import asyncio
import os
import sys
import time

import pytest
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

# Skip under pytest unless explicitly enabled (requires live DashScope API)
pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_GLM5_E2E"),
    reason="Live API test: set RUN_GLM5_E2E=1 to run",
)

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-sp-d56bcb39cd3c43abaf71c536f03df78d")
BASE_URL = os.environ.get("DASHSCOPE_BASE_URL", "https://coding.dashscope.aliyuncs.com/v1")
MODEL = "glm-5"


def section(t: str) -> None:
    print(f"\n{'=' * 60}\n{t}\n{'=' * 60}")


def build_llm(streaming: bool = True) -> ChatOpenAI:
    # Matches kwargs used in backend/app/models/provider.py::get_chat_model
    kwargs = dict(
        model=MODEL,
        api_key=API_KEY,
        base_url=BASE_URL,
        max_tokens=512,
        streaming=streaming,
        temperature=0.3,
        extra_body={"enable_thinking": False},
    )
    if streaming:
        kwargs["stream_usage"] = True
    return ChatOpenAI(**kwargs)


async def test_invoke() -> None:
    section(f"[A] LangChain invoke (non-stream)  model={MODEL}")
    llm = build_llm(streaming=False)
    t = time.time()
    msg = await llm.ainvoke(
        [
            SystemMessage(content="You are a helpful assistant. Answer in one sentence."),
            HumanMessage(content="用中文一句话介绍 Python 语言。"),
        ]
    )
    print(f"latency: {time.time()-t:.2f}s")
    print(f"content: {msg.content}")
    print(f"usage:   {getattr(msg, 'usage_metadata', None)}")


async def test_stream() -> None:
    section(f"[B] LangChain astream  model={MODEL}")
    llm = build_llm(streaming=True)
    t = time.time()
    ttft: float | None = None
    chunks: list[str] = []
    async for ch in llm.astream([HumanMessage(content="Write a Python one-liner that prints 1..10")]):
        if ch.content:
            if ttft is None:
                ttft = time.time() - t
            chunks.append(ch.content if isinstance(ch.content, str) else str(ch.content))
    total = time.time() - t
    print(f"TTFT={ttft}  total={total:.2f}s  chars={len(''.join(chunks))}")
    print("----- stream output -----")
    print("".join(chunks))


@tool
def add(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    return a + b


@tool
def multiply(a: int, b: int) -> int:
    """Multiply two integers and return the product."""
    return a * b


async def test_tool_binding() -> None:
    section(f"[C] LangChain bind_tools  model={MODEL}")
    llm = build_llm(streaming=False).bind_tools([add, multiply])
    msg = await llm.ainvoke(
        [HumanMessage(content="Compute 17 + 25 using the add tool. Do not compute manually.")]
    )
    print(f"content:    {msg.content!r}")
    print(f"tool_calls: {msg.tool_calls}")
    if msg.tool_calls:
        tc = msg.tool_calls[0]
        if tc["name"] == "add":
            out = add.invoke(tc["args"])
            print(f"executed add({tc['args']}) -> {out}")


async def main() -> None:
    print(f"Base URL: {BASE_URL}")
    print(f"Model:    {MODEL}")
    await test_invoke()
    await test_stream()
    await test_tool_binding()
    print("\nAll tests finished.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        print(f"\nFATAL {type(e).__name__}: {e}")
        sys.exit(1)
