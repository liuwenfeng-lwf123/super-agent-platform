from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Callable

from app.agents.store import thread_store
from app.agents.super_agent import super_agent
from app.agents.tools import clear_tool_filter, set_thread_context, set_tool_filter
from app.models.schemas import Message, Thread


@dataclass
class HeadlessCLIConfig:
    message: str
    thread_id: str | None = None
    model: str | None = None
    mode: str = "standard"
    skills: list[str] = field(default_factory=list)
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=list)
    enable_speculation: bool | None = None


def _split_tool_names(values: list[str] | None) -> list[str]:
    if not values:
        return []
    names: list[str] = []
    for value in values:
        if not value:
            continue
        for item in value.split(","):
            name = item.strip()
            if name:
                names.append(name)
    return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Super Agent in headless CLI mode.")
    parser.add_argument("prompt", nargs="?", help="Prompt to send to the agent. If omitted, stdin is used when piped.")
    parser.add_argument("--message", dest="message", help="Explicit prompt to send to the agent.")
    parser.add_argument("--thread-id", help="Reuse or create a specific thread/workspace ID.")
    parser.add_argument("--model", help="Model override.")
    parser.add_argument("--mode", default="standard", help="Agent mode (standard/pro/flash/ultra/etc).")
    parser.add_argument("--skill", dest="skills", action="append", default=[], help="Activate a skill by name. Repeat to add more than one.")
    parser.add_argument("--allowed-tools", "--allowedTools", dest="allowed_tools", action="append", default=[], help="Comma-separated or repeated list of allowed tool names.")
    parser.add_argument("--disallowed-tools", "--disallowedTools", dest="disallowed_tools", action="append", default=[], help="Comma-separated or repeated list of disallowed tool names.")
    parser.add_argument("--enable-speculation", action="store_true", help="Force-enable prompt speculation.")
    parser.add_argument("--disable-speculation", action="store_true", help="Force-disable prompt speculation.")
    parser.add_argument("--json-events", action="store_true", help="Stream raw JSON events to stdout instead of only the final text.")
    return parser


def config_from_args(args: argparse.Namespace) -> tuple[HeadlessCLIConfig, bool]:
    message = (args.message or args.prompt or "").strip()
    if not message and not sys.stdin.isatty():
        message = sys.stdin.read().strip()
    if not message:
        raise SystemExit("No prompt provided. Pass a prompt argument, --message, or pipe stdin.")

    speculation: bool | None = None
    if args.enable_speculation:
        speculation = True
    elif args.disable_speculation:
        speculation = False

    config = HeadlessCLIConfig(
        message=message,
        thread_id=(args.thread_id or None),
        model=(args.model or None),
        mode=(args.mode or "standard"),
        skills=[value for value in args.skills if value],
        allowed_tools=_split_tool_names(args.allowed_tools) or None,
        disallowed_tools=_split_tool_names(args.disallowed_tools),
        enable_speculation=speculation,
    )
    return config, bool(args.json_events)


async def _ensure_thread(thread_id: str | None, title_hint: str) -> Thread:
    if thread_id:
        existing = await thread_store.get(thread_id)
        if existing is not None:
            return existing
        created = Thread(id=thread_id, title=title_hint[:50] or "Headless CLI")
        await thread_store.update_thread(created)
        return created
    return await thread_store.create(title=title_hint[:50] or "Headless CLI")


async def run_headless(
    config: HeadlessCLIConfig,
    *,
    event_handler: Callable[[dict], None] | None = None,
) -> dict:
    thread = await _ensure_thread(config.thread_id, config.message)
    history_messages = list(thread.messages)
    user_msg = Message(role="user", content=config.message, thread_id=thread.id)
    await thread_store.add_message(thread.id, user_msg)

    set_thread_context(thread.id)
    filter_token = set_tool_filter(config.allowed_tools, config.disallowed_tools)
    full_content: list[str] = []
    events: list[dict] = []
    usage = None
    error = None
    saw_done = False

    try:
        async for event_str in super_agent.handle_message(
            message=config.message,
            thread_messages=history_messages,
            model=config.model,
            skills=config.skills or None,
            mode=config.mode,
            thread_id=thread.id,
            enable_speculation=config.enable_speculation,
        ):
            try:
                event = json.loads(event_str)
            except json.JSONDecodeError:
                event = {"type": "raw", "content": event_str}
            events.append(event)
            if event_handler is not None:
                event_handler(event)
            if event.get("type") == "token":
                full_content.append(event.get("content", ""))
            elif event.get("type") == "error":
                error = event.get("content") or event.get("error") or "Unknown error"
            elif event.get("type") == "done":
                usage = event.get("usage")
                saw_done = True

        assistant_content = "".join(full_content)
        if saw_done:
            assistant_msg = Message(role="assistant", content=assistant_content, thread_id=thread.id)
            await thread_store.add_message(thread.id, assistant_msg)

        return {
            "thread_id": thread.id,
            "output": assistant_content,
            "events": events,
            "usage": usage,
            "error": error,
        }
    finally:
        clear_tool_filter(filter_token)


async def _run_and_print(config: HeadlessCLIConfig, json_events: bool) -> int:
    if json_events:
        result = await run_headless(
            config,
            event_handler=lambda payload: print(json.dumps(payload, ensure_ascii=False), flush=True),
        )
    else:
        result = await run_headless(config)
        if result["output"]:
            print(result["output"])

    if result.get("error") and not result.get("output"):
        print(result["error"], file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config, json_events = config_from_args(args)
    return asyncio.run(_run_and_print(config, json_events))


if __name__ == "__main__":
    raise SystemExit(main())
