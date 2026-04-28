"""
Local Direct Mode — run the agent loop entirely in-process, no backend server needed.

Usage:
    python -m app.local.direct "Build a hello world Flask app"
    python -m app.local.direct --model gpt-4o "Fix the bug in main.py"
    echo "Refactor this module" | python -m app.local.direct

This combines:
  - The SuperAgent loop (LLM + tool calls)
  - Local tool execution (bash, file I/O, etc.) directly in-process
  - Sandbox manager for file tracking
  - Memory store for context persistence

No WebSocket, no FastAPI server, no network round-trips.
"""
import argparse
import asyncio
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# ── Ensure backend package is importable ──
_backend_root = os.path.join(os.path.dirname(__file__), "..", "..")
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)


async def _execute_local_bash(command: str, cwd: str | None = None, timeout: int = 120) -> dict:
    """Execute a bash command locally, returning structured result."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or os.getcwd(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"success": False, "error": f"Command timed out ({timeout}s)"}
        return {
            "success": proc.returncode == 0,
            "output": stdout.decode("utf-8", errors="replace")[:50000],
            "error": stderr.decode("utf-8", errors="replace")[:10000],
            "exit_code": proc.returncode,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_system_info() -> dict:
    """Gather local system information for the agent context."""
    disk = shutil.disk_usage("/")
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "hostname": platform.node(),
        "arch": platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "disk_free_gb": round(disk.free / (1024**3), 1),
        "home_dir": str(Path.home()),
        "cwd": os.getcwd(),
    }


async def run_direct(
    message: str,
    *,
    model: str | None = None,
    mode: str = "standard",
    skills: list[str] | None = None,
    thread_id: str | None = None,
    work_dir: str | None = None,
    json_events: bool = False,
    event_callback=None,
) -> dict:
    """Run the full agent loop locally in a single process.

    Returns:
        dict with keys: thread_id, output, events, usage, error
    """
    from app.agents.store import thread_store
    from app.agents.super_agent import super_agent
    from app.agents.tools import set_thread_context, set_tool_filter, clear_tool_filter
    from app.models.schemas import Message, Thread

    # Inject local system context into the message
    sys_info = _get_system_info()
    local_context = (
        f"\n\n[Local Direct Mode | OS: {sys_info['os']} {sys_info['arch']} | "
        f"CWD: {work_dir or sys_info['cwd']} | Python: {sys_info['python_version']}]"
    )

    # Ensure thread
    if thread_id:
        thread = await thread_store.get(thread_id)
        if thread is None:
            thread = Thread(id=thread_id, title=message[:50] or "Direct Mode")
            await thread_store.update_thread(thread)
    else:
        thread = await thread_store.create(title=message[:50] or "Direct Mode")

    history = list(thread.messages)
    user_msg = Message(role="user", content=message, thread_id=thread.id)
    await thread_store.add_message(thread.id, user_msg)

    set_thread_context(thread.id)
    filter_token = set_tool_filter(None, None)

    full_content: list[str] = []
    events: list[dict] = []
    usage = None
    error = None

    try:
        async for event_str in super_agent.handle_message(
            message=message + local_context,
            thread_messages=history,
            model=model,
            skills=skills,
            mode=mode,
            thread_id=thread.id,
        ):
            try:
                event = json.loads(event_str)
            except json.JSONDecodeError:
                event = {"type": "raw", "content": event_str}

            events.append(event)

            if event_callback:
                event_callback(event)

            etype = event.get("type")
            if etype == "token":
                content = event.get("content", "")
                full_content.append(content)
                if not json_events:
                    print(content, end="", flush=True)
            elif etype == "tool_call":
                tool = event.get("data", {}).get("tool", "?")
                if not json_events:
                    print(f"\n  🔧 {tool}...", end="", flush=True)
            elif etype == "tool_result":
                data = event.get("data", {})
                status = data.get("status", "done")
                if not json_events:
                    mark = "✓" if status != "error" else "✗"
                    print(f" [{mark}]", end="", flush=True)
            elif etype == "file_diff":
                if not json_events:
                    path = event.get("data", {}).get("path", "?")
                    print(f"\n  📝 {path}", end="", flush=True)
            elif etype == "error":
                error = event.get("content") or "Unknown error"
                if not json_events:
                    print(f"\n  ❌ Error: {error}", flush=True)
            elif etype == "done":
                usage = event.get("usage")
            elif etype == "raw":
                pass  # ignore

            if json_events:
                print(json.dumps(event, ensure_ascii=False), flush=True)

        if not json_events:
            print()  # newline after stream

        assistant_content = "".join(full_content)
        if assistant_content:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TianGongFlow Direct Mode — run the agent locally without a server.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m app.local.direct "Create a Python script that counts words"
  python -m app.local.direct --model gpt-4o --mode pro "Refactor utils.py"
  echo "Fix the tests" | python -m app.local.direct
  python -m app.local.direct --json-events "Hello world"
        """,
    )
    parser.add_argument("prompt", nargs="?", help="Prompt to send")
    parser.add_argument("--message", help="Explicit prompt (alternative to positional)")
    parser.add_argument("--model", help="Model override")
    parser.add_argument("--mode", default="standard", help="Agent mode (standard/pro/flash/ultra)")
    parser.add_argument("--skill", dest="skills", action="append", default=[], help="Activate a skill")
    parser.add_argument("--thread-id", help="Reuse a thread/workspace")
    parser.add_argument("--work-dir", help="Working directory (default: cwd)")
    parser.add_argument("--json-events", action="store_true", help="Stream raw JSON events")

    args = parser.parse_args(argv)

    message = (args.message or args.prompt or "").strip()
    if not message and not sys.stdin.isatty():
        message = sys.stdin.read().strip()
    if not message:
        parser.print_help()
        return 1

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    result = asyncio.run(run_direct(
        message,
        model=args.model,
        mode=args.mode,
        skills=args.skills or None,
        thread_id=args.thread_id,
        work_dir=args.work_dir,
        json_events=args.json_events,
    ))

    if result.get("error") and not result.get("output"):
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
