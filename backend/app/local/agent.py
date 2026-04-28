from app.local.tools import LOCAL_TOOLS
from app.agents.tools import extract_validation_result, get_all_tools, set_thread_context
from app.local.editor_state import build_editor_context_prompt
from app.local.tools import set_local_thread_context
from app.models.provider import llm_provider
from app.memory.store import memory_store
from app.agents.context import should_summarize, summarize_messages, get_messages_for_context
from app.agents.tool_runtime import clear_runtime_context, consume_permission_events, consume_tool_use_summary, set_runtime_context, wrap_langchain_tool
from app.skills.search import web_search_tool
from app.models.schemas import Message
from app.config import settings
from app.agents.intent_detect import (
    extract_direct_search_query as _extract_direct_search_query,
    message_requests_screenshot as _message_requests_screenshot,
    extract_direct_browser_screenshot_url as _extract_direct_browser_screenshot_url,
    extract_direct_browser_open_url as _extract_direct_browser_open_url,
    is_simple_open_screenshot_request as _is_simple_open_screenshot_request,
    is_standalone_screenshot_request as _is_standalone_screenshot_request,
    screenshot_target_for_message as _screenshot_target_for_message,
)
try:
    from langchain.agents import create_agent as create_react_agent
except ImportError:  # pragma: no cover - older langchain fallback
    from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
import asyncio
import os
import json
import logging
import re
from typing import AsyncGenerator


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lightweight LLM intent classifier
# ---------------------------------------------------------------------------
_INTENT_CLASSIFY_SYSTEM = """You are a fast intent classifier for a desktop‑control AI agent.
Given the user message, return ONE JSON object (no markdown, no explanation).

Categories (pick exactly one):
- open_app   → {"intent":"open_app","app_name":"<macOS English app name, e.g. Terminal>"}
- screenshot → {"intent":"screenshot","target":"screen"} or {"intent":"screenshot","target":"browser","url":"<url if mentioned>"}
- browser    → {"intent":"browser","url":"<url>"}
- search     → {"intent":"search","query":"<search keywords>"}
- list_files → {"intent":"list_files","path":"<directory, default ~>"}
- general    → {"intent":"general"}

Rules:
- Translate Chinese app names to macOS English names (终端→Terminal, 浏览器→Google Chrome, 访达→Finder, 备忘录→Notes, etc.)
- If the message is ambiguous or conversational, return general.
- If the message involves MULTIPLE steps or actions (e.g. "read file X, analyze it, and save results"), return general so the full agent can handle it.
- ONLY output JSON."""


async def _classify_intent(message: str, model: str | None) -> dict:
    """Call LLM with a tiny prompt to classify the user's intent (< 1 s)."""
    try:
        llm = llm_provider.get_chat_model(model, streaming=False)
        resp = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content=_INTENT_CLASSIFY_SYSTEM),
                HumanMessage(content=message),
            ]),
            timeout=8.0,
        )
        text = resp.content.strip()
        # Strip markdown fences if model wraps in ```json ... ```
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        logger.info("Intent classifier: %s → %s", message[:60], result.get("intent"))
        return result
    except asyncio.TimeoutError:
        logger.warning("Intent classifier timed out for: %s", message[:60])
        return {"intent": "general"}
    except Exception as exc:
        logger.warning("Intent classifier failed: %s — falling through to general", exc)
        return {"intent": "general"}


_session_approved: dict[str, set[str]] = {}


async def _check_shortcut_permission(
    client,
    thread_id: str,
    tool_name: str,
    tool_input: dict,
    description: str,
) -> bool:
    """Web‑UI permission gate for shortcut actions.

    - client._auto_approve is True  → allow immediately (no popup).
    - Tool already approved in this thread session → allow without popup.
    - Otherwise, push a permission_request to the frontend and wait.
    Returns True if allowed, False if denied.
    """
    if client._auto_approve or tool_name in client._tool_auto_approve:
        return True

    if thread_id and tool_name in _session_approved.get(thread_id, set()):
        return True

    from app.agents.tool_runtime import permission_requests

    result = await permission_requests.request_permission(
        thread_id=thread_id,
        agent_id="local_agent",
        mode="local",
        tool_name=tool_name,
        tool_input=tool_input,
        reason=description,
        source="intent_classifier",
    )
    if result.decision == "allow" and thread_id:
        _session_approved.setdefault(thread_id, set()).add(tool_name)
    return result.decision == "allow"


async def _yield_text_chunks(text: str, chunk_size: int = 80) -> AsyncGenerator[str, None]:
    for index in range(0, len(text), chunk_size):
        yield json.dumps({"type": "token", "content": text[index:index + chunk_size]})
        await asyncio.sleep(0.01)


LOCAL_MODE_SYSTEM = """You are a Super Agent in LOCAL MODE. You ARE connected to the user's computer RIGHT NOW through a local client. You CAN see and operate their computer.

When the user asks about your abilities, DO NOT explain what you can't do. Instead, DEMONSTRATE by using tools. For example:
- If user asks "can you see my computer?", call local_get_system_info immediately and show the results.
- If user asks "what's on my computer?", call local_list_files on their home directory.

AVAILABLE TOOLS (use them actively, don't just describe them):

== System & Files ==
- local_get_system_info: Get OS, hostname, disk, memory info. USE THIS FIRST to prove you're connected.
- local_execute_bash: Run any terminal command on the user's computer
- local_read_file: Read files. Supports line ranges (start_line, end_line) to read specific parts of large files.
- local_write_file: Create or overwrite entire files
- local_edit_file: **PREFERRED for code changes** — precise find-and-replace edit. Only changes the exact part you specify. Always read the file first, then use this.
- local_list_files: Browse the user's file system
- local_execute_python: Run Python code with the user's local environment

== Code Intelligence ==
- local_search_code: Search for patterns in files using ripgrep/grep. Returns file paths + line numbers. Use include="*.py" to filter by type.
- local_project_index: Analyze project structure — file tree + language stats. Use this FIRST on a new codebase.
- local_git: Run any git command (status, diff, log, commit, branch, push, pull, etc.)

== Desktop Control ==
- local_open_app: Open applications (Chrome, VS Code, Finder, Terminal, etc.)
- local_read_clipboard: Read the user's clipboard contents
- local_write_clipboard: Copy text to the user's clipboard
- local_send_notification: Send a desktop notification to the user
- local_manage_window: Move, resize, minimize, maximize, fullscreen, or close app windows (macOS)

== Scheduling ==
- local_create_schedule: Create a recurring or one-time scheduled reminder/task
- local_list_schedules: List all scheduled tasks
- local_delete_schedule: Delete a scheduled task by ID

== Other ==
- get_editor_state / get_editor_diagnostics: Read the current thread's active editor file, cursor, selection, and recent editor diagnostics when available
- web_search: Search the internet
- web_fetch: Read web pages
- calculate: Math calculations
- get_current_time: Current date/time

RULES:
1. ALWAYS use tools to answer questions about the user's computer. Never say "I cannot" - you CAN.
2. When first connected, call local_get_system_info to learn about the computer.
3. Be careful with destructive commands (rm, format, etc.) - warn the user first.
4. Use the user's actual file paths (check with local_list_files or local_get_system_info first).
5. Respond in the same language as the user.
6. DO NOT give disclaimers about not being able to access the computer. You ARE connected.
7. After completing any operation, give a SHORT one-line Chinese summary of what you did, e.g. "已在桌面创建 report.txt（1.2KB）" or "已执行 brew update，更新了 3 个包".
8. For long-running tasks (>10 seconds), use local_send_notification to alert the user when done.
9. **Code editing workflow**: When modifying code files, ALWAYS: (a) read the file first, (b) use local_edit_file for precise changes, (c) never rewrite an entire file just to change a few lines. For large files, use start_line/end_line to read only the relevant section.
10. **Codebase exploration**: When the user asks about a project, use local_project_index first to understand the structure, then local_search_code to find specific code, then local_read_file to examine details.
"""

MEMORY_RECALL_GUARDRAILS = """

## Memory Recall Safeguards
- Memory can be stale. Treat it as historical context, not current truth.
- If memory references a file, function, flag, or behavior, verify it against the current project state before relying on it.
- If the user asks to ignore memory, do not use remembered facts in your answer.
- Prefer what you can read from the current code and files over remembered snapshots.
"""


def _format_local_error(error: Exception | str, model: str | None = None) -> str:
    message = str(error)
    lower = message.lower()
    config = llm_provider.resolve_model_config(model)
    if "invalid_api_key" in lower or "invalid api key" in lower or "401" in lower or "incorrect api key" in lower:
        return (
            "**API Key 无效或已被接口拒绝。**\n\n"
            f"当前模型：`{config.display_name}` / `{config.model}`\n\n"
            f"Provider：`{config.provider}`\n\n"
            f"接口地址：`{config.base_url}`\n\n"
            f"请检查 `backend/.env` 里的 `{config.api_key_env}` 是否是这个接口可用的 Key，修改后重启后端。"
        )
    if "connection error" in lower or "api connection" in lower or "connecterror" in lower or "timed out" in lower or "timeout" in lower:
        return (
            "**模型接口连接失败。**\n\n"
            f"当前模型：`{config.display_name}` / `{config.model}`\n\n"
            f"Provider：`{config.provider}`\n\n"
            f"接口地址：`{config.base_url}`\n\n"
            "这通常表示后端没有成功连到模型接口。请先切到「标准」模式测试同一个模型；如果标准模式也失败，请检查网络、接口地址和 Galaxy 服务状态。"
        )
    if "client disconnected" in lower or "send failed" in lower or "websocket" in lower:
        return (
            "**本地客户端连接断开。**\n\n"
            "请确认顶部显示有绿色「1 个客户端」，然后重新绑定当前会话；如果仍失败，请重启本地客户端。"
        )
    if "permission denied" in lower or "not permitted" in lower or "operation not permitted" in lower:
        return (
            "**权限不足。**\n\n"
            "你的电脑拒绝了这个操作，可能需要管理员权限。\n\n"
            "建议：在终端里用 `sudo` 执行，或检查文件/文件夹权限。"
        )
    if "no such file" in lower or "file not found" in lower or "filenotfounderror" in lower or "enoent" in lower:
        return (
            "**找不到文件或目录。**\n\n"
            "请检查路径是否正确。提示：可以先说「列出文件」确认目录内容。"
        )
    if "command not found" in lower or "not recognized" in lower:
        cmd = message.split(":")[-1].strip().split()[0] if ":" in message else "命令"
        return (
            f"**找不到命令 `{cmd}`。**\n\n"
            "可能没安装或不在 PATH 里。建议：\n"
            "- 确认拼写是否正确\n"
            "- 用 `which {cmd}` 或 `brew install {cmd}` 试试"
        )
    if "disk" in lower and ("full" in lower or "no space" in lower):
        return "**磁盘空间不足。**\n\n请清理磁盘后重试。可以说「查看系统信息」确认磁盘使用情况。"
    if "browser" in lower and ("closed" in lower or "disposed" in lower):
        return (
            "**浏览器已关闭。**\n\n"
            "之前打开的浏览器窗口已经关了。请重新发送指令，系统会自动打开新窗口。"
        )
    if "rate limit" in lower or "429" in lower or "too many requests" in lower:
        return "**请求太频繁，被接口限流了。**\n\n请等几秒再试。"
    return message


def _should_ignore_memory(text: str) -> bool:
    lowered = text.lower()
    triggers = [
        "ignore memory",
        "don't use memory",
        "do not use memory",
        "忽略记忆",
        "不要用记忆",
        "别用记忆",
    ]
    return any(trigger in lowered for trigger in triggers)


def _is_local_status_question(text: str) -> bool:
    lowered = text.lower()
    if any(trigger in lowered for trigger in ["控制", "操作", "执行", "运行", "打开", "写入", "删除", "修改"]):
        return False
    return any(
        trigger in lowered
        for trigger in [
            "能看到我的电脑",
            "能看得到我的电脑",
            "看得到我的电脑",
            "看到我的电脑",
            "连接到我的电脑",
            "local client",
            "本地客户端",
            "系统信息",
        ]
    )


def _is_local_control_capability_question(text: str) -> bool:
    lowered = text.lower()
    has_question = any(trigger in lowered for trigger in ["能", "可以", "会", "能不能", "可不可以", "?"])
    has_control = any(trigger in lowered for trigger in ["控制我的电脑", "操作我的电脑", "控制电脑", "操作电脑", "执行命令", "打开应用", "打开我的软件", "打开软件"])
    return has_question and has_control


def _is_local_meta_question(text: str) -> bool:
    lowered = text.lower()
    return any(
        trigger in lowered
        for trigger in [
            "怎么一直重复",
            "为什么一直重复",
            "一直重复",
            "你到底行不行",
            "行不行",
            "怎么又报错",
            "connection error",
            "模型接口连接失败",
        ]
    )


def _extract_list_files_path(text: str) -> str | None:
    lowered = text.lower()
    if not any(trigger in text for trigger in ["列出", "查看", "看看"]) and "list" not in lowered:
        return None
    if "桌面" in text or "desktop" in lowered:
        return "~/Desktop"
    if "下载" in text or "downloads" in lowered:
        return "~/Downloads"
    if "主目录" in text or "home" in lowered:
        return "~"
    for marker in ["列出", "查看", "看看", "list"]:
        if marker in text:
            candidate = text.split(marker, 1)[1]
            for suffix in ["下的文件", "里的文件", "的文件", "文件", "目录", "里面有什么", "有什么"]:
                candidate = candidate.replace(suffix, "")
            candidate = candidate.strip(" ：:，,。`'\"")
            if candidate:
                return candidate
    return None


def _extract_open_app_name(text: str) -> str | None:
    lowered = text.lower()
    if "打开" not in text and "open " not in lowered:
        return None
    if any(mark in text for mark in ["吗", "么", "？", "?"]) or any(trigger in lowered for trigger in ["can you", "could you"]):
        return None
    app = text.replace("打开", "").replace("应用", "").strip(" ：:，,。`'\"")
    if lowered.startswith("open "):
        app = text[5:].strip(" ：:，,。`'\"")
    # Strip common possessive / filler prefixes: "我的终端" → "终端"
    for prefix in ("我的", "一下", "那个", "这个"):
        if app.startswith(prefix):
            app = app[len(prefix):]
    app = app.strip()
    aliases = {
        "访达": "Finder",
        "finder": "Finder",
        "软件": "",
        "我的软件": "",
        "浏览器": "Google Chrome",
        "chrome": "Google Chrome",
        "谷歌浏览器": "Google Chrome",
        "safari": "Safari",
        "终端": "Terminal",
        "terminal": "Terminal",
        "iterm": "iTerm",
        "iterm2": "iTerm",
        "vscode": "Visual Studio Code",
        "vs code": "Visual Studio Code",
        "code": "Visual Studio Code",
        "微信": "WeChat",
        "wechat": "WeChat",
        "备忘录": "Notes",
        "日历": "Calendar",
        "邮件": "Mail",
        "音乐": "Music",
        "计算器": "Calculator",
        "活动监视器": "Activity Monitor",
        "系统设置": "System Settings",
        "系统偏好设置": "System Preferences",
    }
    return aliases.get(app.lower(), app) if app else None




def _is_tool_not_disabled(tool_name: str, disabled: list[str] | None = None) -> bool:
    return tool_name not in {name for name in (disabled or []) if name}


def _is_clipboard_read_request(message: str) -> bool:
    lowered = message.lower()
    return any(trigger in lowered for trigger in [
        "剪贴板", "剪切板", "粘贴板", "clipboard",
        "读取剪贴", "看看剪贴", "获取剪贴", "剪贴板的内容",
        "我复制的", "刚复制的", "刚才复制",
    ])


def _is_clipboard_write_request(message: str) -> str | None:
    lowered = message.lower()
    if not any(trigger in lowered for trigger in [
        "复制到剪贴板", "写入剪贴板", "拷贝到剪贴板",
        "copy to clipboard", "放到剪贴板", "存到剪贴板",
    ]):
        return None
    return message




def _format_screenshot_chat_content(result: str) -> str:
    match = re.search(r"(/tmp/screenshot_[A-Za-z0-9_.-]+\.png)", result)
    if not match:
        return result
    filename = os.path.basename(match.group(1))
    url = f"/api/screenshots/{filename}"
    return f"截图完成：\n\n![截图预览]({url})\n\n[打开原图]({url})"


def _format_search_results(query: str, results: list[dict]) -> str:
    if not results:
        return f"没有搜到：`{query}`"
    lines = [f"**搜索结果：`{query}`**\n"]
    for index, result in enumerate(results, 1):
        title = result.get("title") or "无标题"
        body = result.get("body") or ""
        href = result.get("href") or ""
        lines.append(f"### {index}. {title}")
        if body:
            lines.append(body)
        if href:
            lines.append(f"[打开链接]({href})")
        lines.append("")
    lines.append("你可以继续说：`打开第 1 条` 或 `总结第 1 条`。")
    return "\n".join(lines).strip()


def _filter_tools(tools: list, allowed: list[str] | None = None, disabled: list[str] | None = None) -> list:
    allowed_set = {name for name in (allowed or []) if name}
    disabled_set = {name for name in (disabled or []) if name}
    filtered = []
    for tool_obj in tools:
        name = getattr(tool_obj, "name", "")
        if allowed_set and name not in allowed_set:
            continue
        if disabled_set and name in disabled_set:
            continue
        filtered.append(tool_obj)
    return filtered


def _is_tool_enabled(tool_name: str, allowed: list[str] | None = None, disabled: list[str] | None = None) -> bool:
    allowed_set = {name for name in (allowed or []) if name}
    disabled_set = {name for name in (disabled or []) if name}
    return (not allowed_set or tool_name in allowed_set) and tool_name not in disabled_set


class LocalAgent:

    async def _try_shortcut(
        self,
        message: str,
        client,
        thread_id: str,
        tools: list[str] | None,
        disabled_tools: list[str] | None,
    ) -> AsyncGenerator[str, None] | None:
        """Try to handle the message via a fast shortcut path.

        Yields SSE chunks if handled. Returns None if should fall through to LLM.
        """
        from app.local.gateway import local_gateway

        # ── Direct browser screenshot ──
        direct_browser_screenshot_url = _extract_direct_browser_screenshot_url(message)
        if direct_browser_screenshot_url and _is_simple_open_screenshot_request(message) and _is_tool_not_disabled("browser_open_and_screenshot", disabled_tools) and _is_tool_not_disabled("screenshot", disabled_tools):
            async def _gen():
                allowed = await _check_shortcut_permission(client, thread_id, "screenshot", {"url": direct_browser_screenshot_url}, f"打开 {direct_browser_screenshot_url} 并截图")
                if not allowed:
                    async for c in _yield_text_chunks(f"**打开 `{direct_browser_screenshot_url}` 并截图 被拒绝。**"): yield c
                    yield json.dumps({"type": "done"}); return
                rt = set_runtime_context(thread_id=thread_id, mode="local")
                try:
                    from app.agents.system_tools import browser_open_and_screenshot
                    from app.agents.tool_runtime import record_tool_event
                    si = {"url": direct_browser_screenshot_url, "region": "full", "timeout": 30}
                    yield json.dumps({"type": "tool_call", "data": {"tool": "browser_open_and_screenshot", "input": json.dumps(si, ensure_ascii=False), "status": "running"}})
                    sr = await asyncio.to_thread(browser_open_and_screenshot.invoke, si)
                    record_tool_event(tool="browser_open_and_screenshot", category="system", thread_id=thread_id, mode="local", input_preview=json.dumps(si, ensure_ascii=False), output_preview=str(sr)[:1000], success=True, source="direct-local")
                    yield json.dumps({"type": "tool_result", "data": {"tool": "browser_open_and_screenshot", "status": "completed", "output": str(sr)[:500]}})
                    yield json.dumps({"type": "token", "content": _format_screenshot_chat_content(str(sr))})
                    yield json.dumps({"type": "done"})
                finally:
                    clear_runtime_context(rt)
            return _gen()

        # ── Standalone screenshot ──
        if _is_standalone_screenshot_request(message) and _is_tool_not_disabled("screenshot", disabled_tools):
            async def _gen():
                target = _screenshot_target_for_message(message)
                allowed = await _check_shortcut_permission(client, thread_id, "screenshot", {"target": target}, "截取屏幕截图")
                if not allowed:
                    async for c in _yield_text_chunks("**截图被拒绝。**"): yield c
                    yield json.dumps({"type": "done"}); return
                rt = set_runtime_context(thread_id=thread_id, mode="local")
                try:
                    from app.agents.system_tools import screenshot
                    from app.agents.tool_runtime import record_tool_event
                    si = {"region": "full", "wait_for_browser": target == "browser", "browser": "Safari", "timeout": 8, "target": target}
                    yield json.dumps({"type": "tool_call", "data": {"tool": "screenshot", "input": json.dumps(si, ensure_ascii=False), "status": "running"}})
                    sr = await asyncio.to_thread(screenshot.invoke, si)
                    record_tool_event(tool="screenshot", category="system", thread_id=thread_id, mode="local", input_preview=json.dumps(si, ensure_ascii=False), output_preview=str(sr)[:1000], success=True, source="direct-current-page-local")
                    yield json.dumps({"type": "tool_result", "data": {"tool": "screenshot", "status": "completed", "output": str(sr)[:500]}})
                    yield json.dumps({"type": "token", "content": _format_screenshot_chat_content(str(sr))})
                    yield json.dumps({"type": "done"})
                finally:
                    clear_runtime_context(rt)
            return _gen()

        # ── Clipboard read ──
        if _is_clipboard_read_request(message) and _is_tool_not_disabled("local_read_clipboard", disabled_tools):
            async def _gen():
                allowed = await _check_shortcut_permission(client, thread_id, "local_read_clipboard", {}, "读取剪贴板内容")
                if not allowed:
                    async for c in _yield_text_chunks("**读取剪贴板被拒绝。**"): yield c
                    yield json.dumps({"type": "done"}); return
                result = await client.send_request("read_clipboard", {}, timeout=10, force_auto_approve=True)
                local_gateway.add_audit(client.client_id, "read_clipboard", {}, result, thread_id=thread_id)
                if result.get("success"):
                    cb = result.get("content", "")
                    content = f"**剪贴板内容：**\n```\n{cb[:3000]}\n```" if cb else "**剪贴板是空的。**"
                else:
                    content = f"**读取剪贴板失败：** {result.get('error', '未知错误')}"
                async for c in _yield_text_chunks(content): yield c
                yield json.dumps({"type": "done"})
            return _gen()

        # ── Direct browser open ──
        direct_browser_open_url = _extract_direct_browser_open_url(message)
        if direct_browser_open_url and _is_tool_not_disabled("browser_open", disabled_tools):
            async def _gen():
                allowed = await _check_shortcut_permission(client, thread_id, "browser_open", {"url": direct_browser_open_url, "browser": "Safari"}, f"打开网页 {direct_browser_open_url}")
                if not allowed:
                    async for c in _yield_text_chunks(f"**打开网页 `{direct_browser_open_url}` 被拒绝。**"): yield c
                    yield json.dumps({"type": "done"}); return
                rt = set_runtime_context(thread_id=thread_id, mode="local")
                try:
                    from app.agents.system_tools import browser_open
                    from app.agents.tool_runtime import record_tool_event
                    oi = {"url": direct_browser_open_url, "browser": "Safari"}
                    yield json.dumps({"type": "tool_call", "data": {"tool": "browser_open", "input": json.dumps(oi, ensure_ascii=False), "status": "running"}})
                    oret = await asyncio.to_thread(browser_open.invoke, oi)
                    record_tool_event(tool="browser_open", category="system", thread_id=thread_id, mode="local", input_preview=json.dumps(oi, ensure_ascii=False), output_preview=str(oret)[:1000], success=True, source="direct-open-local")
                    yield json.dumps({"type": "tool_result", "data": {"tool": "browser_open", "status": "completed", "output": str(oret)[:500]}})
                    yield json.dumps({"type": "token", "content": f"已打开：{direct_browser_open_url}"})
                    yield json.dumps({"type": "done"})
                finally:
                    clear_runtime_context(rt)
            return _gen()

        # ── System status ──
        if _is_local_status_question(message):
            if not _is_tool_enabled("local_get_system_info", tools, disabled_tools):
                return None
            async def _gen():
                allowed = await _check_shortcut_permission(client, thread_id, "local_get_system_info", {}, "查看本地系统信息")
                if not allowed:
                    async for c in _yield_text_chunks("**查看本地系统信息被拒绝。**"): yield c
                    yield json.dumps({"type": "done"}); return
                info = client.info or {}
                result = await client.send_request("get_system_info", {}, timeout=30, force_auto_approve=True)
                local_gateway.add_audit(client.client_id, "get_system_info", {}, result, thread_id=thread_id)
                system_info = result.get("info") if result.get("success") else ""
                content = (
                    "**可以，我已经连接到你的本地客户端。**\n\n"
                    f"- 主机：`{info.get('hostname', client.client_id)}`\n"
                    f"- 系统：`{info.get('os', 'unknown')}`\n"
                    f"- 架构：`{info.get('arch', 'unknown')}`\n"
                    f"- Home：`{info.get('home', 'unknown')}`"
                )
                if system_info:
                    content += f"\n\n本地客户端返回：\n```text\n{str(system_info)[:1200]}\n```"
                async for c in _yield_text_chunks(content): yield c
                yield json.dumps({"type": "done"})
            return _gen()

        return None  # no shortcut matched

    async def handle_message(
        self,
        message: str,
        thread_messages: list[Message],
        model: str | None = None,
        tools: list[str] | None = None,
        disabled_tools: list[str] | None = None,
        thread_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        from app.local.gateway import local_gateway
        client = local_gateway.get_client_for_thread(thread_id or "")
        if not client:
            yield json.dumps({
                "type": "token",
                "content": "**没有连接到本地客户端。**\n\n请先在你的电脑上运行本地客户端：\n```bash\npython local_client.py\n```\n然后将客户端绑定到当前线程。",
            })
            yield json.dumps({"type": "done"})
            return

        # ── Fast shortcut paths (screenshot, clipboard, browser, system info) ──
        shortcut_gen = await self._try_shortcut(message, client, thread_id or "", tools, disabled_tools)
        if shortcut_gen is not None:
            async for chunk in shortcut_gen:
                yield chunk
            return

        from app.local.shortcuts import match_shortcut
        matched_shortcut = match_shortcut(message)
        if matched_shortcut:
            steps = matched_shortcut.get("steps", [])
            yield json.dumps({"type": "token", "content": f"**执行快捷指令「{matched_shortcut['name']}」**\n\n"})
            for i, step in enumerate(steps, 1):
                yield json.dumps({"type": "token", "content": f"**步骤 {i}：** {step}\n"})
            yield json.dumps({"type": "token", "content": "\n---\n"})
            combined_message = f"请依次执行以下步骤：\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
            message = combined_message

        if not llm_provider.has_api_key(model):
            yield json.dumps({
                "type": "token",
                "content": "**本地模式需要 API Key。**请在设置中配置后再试。",
            })
            yield json.dumps({"type": "done"})
            return

        # ── LLM intent classifier (replaces hardcoded pattern matching) ──
        intent = await _classify_intent(message, model)
        intent_type = intent.get("intent", "general")

        if intent_type == "open_app":
            app_name = intent.get("app_name", "").strip()
            if app_name and _is_tool_enabled("local_open_app", tools, disabled_tools):
                # ── Web-UI permission gate ──
                allowed = await _check_shortcut_permission(
                    client, thread_id or "", "local_open_app",
                    {"app_name": app_name}, f"打开应用 {app_name}",
                )
                if not allowed:
                    content = f"**打开 `{app_name}` 被拒绝。**\n\n如需打开，请重新发送并在页面弹框中点击「允许」，或开启「自动批准」。"
                    async for chunk in _yield_text_chunks(content):
                        yield chunk
                    yield json.dumps({"type": "done"})
                    return
                result = await client.send_request("open_app", {"app_name": app_name}, timeout=30, force_auto_approve=True)
                local_gateway.add_audit(client.client_id, "open_app", {"app_name": app_name}, result, thread_id=thread_id or "")
                if result.get("success"):
                    content = f"**已打开：**`{app_name}`"
                    async for chunk in _yield_text_chunks(content):
                        yield chunk
                    yield json.dumps({"type": "done"})
                    return
                # Execution error (app not found, timeout) → fall through to LLM
                logger.warning("Classified open_app(%s) failed: %s — falling through to LLM", app_name, result.get("error"))

        elif intent_type == "search":
            query = intent.get("query", "").strip()
            if query and _is_tool_enabled("web_search", tools, disabled_tools):
                try:
                    results = await web_search_tool.search(query, max_results=6)
                    content = _format_search_results(query, results)
                except Exception as exc:
                    content = f"搜索失败：{exc}"
                async for chunk in _yield_text_chunks(content, chunk_size=180):
                    yield chunk
                yield json.dumps({"type": "done"})
                return

        elif intent_type == "list_files":
            raw_path = intent.get("path", "~").strip() or "~"
            if _is_tool_enabled("local_list_files", tools, disabled_tools):
                # ── Web-UI permission gate ──
                expanded_path = os.path.expanduser(raw_path)
                allowed = await _check_shortcut_permission(
                    client, thread_id or "", "local_list_files",
                    {"path": expanded_path}, f"列出目录 {expanded_path}",
                )
                if not allowed:
                    content = f"**列出 `{expanded_path}` 被拒绝。**"
                    async for chunk in _yield_text_chunks(content):
                        yield chunk
                    yield json.dumps({"type": "done"})
                    return
                result = await client.send_request("list_files", {"path": expanded_path}, timeout=30, force_auto_approve=True)
                local_gateway.add_audit(client.client_id, "list_files", {"path": expanded_path}, result, thread_id=thread_id or "")
                if result.get("success"):
                    entries = result.get("entries", [])
                    lines = [f"**已列出 `{expanded_path}`：**"]
                    for entry in entries[:80]:
                        prefix = "目录" if entry.get("is_dir") else "文件"
                        lines.append(f"- {prefix}：`{entry.get('name', '?')}`")
                    if len(entries) > 80:
                        lines.append(f"- 还有 {len(entries) - 80} 项未显示")
                    content = "\n".join(lines)
                    async for chunk in _yield_text_chunks(content):
                        yield chunk
                    yield json.dumps({"type": "done"})
                    return
                logger.warning("Classified list_files(%s) failed: %s — falling through to LLM", expanded_path, result.get("error"))

        elif intent_type == "browser":
            url = intent.get("url", "").strip()
            if url and _is_tool_not_disabled("browser_open", disabled_tools):
                allowed = await _check_shortcut_permission(
                    client, thread_id or "", "browser_open",
                    {"url": url, "browser": "Safari"}, f"打开网页 {url}",
                )
                if not allowed:
                    content = f"**打开网页 `{url}` 被拒绝。**"
                    async for chunk in _yield_text_chunks(content):
                        yield chunk
                    yield json.dumps({"type": "done"})
                    return
                runtime_token = set_runtime_context(thread_id=thread_id or "", mode="local")
                try:
                    from app.agents.system_tools import browser_open
                    from app.agents.tool_runtime import record_tool_event
                    open_input = {"url": url, "browser": "Safari"}
                    yield json.dumps({"type": "tool_call", "data": {"tool": "browser_open", "input": json.dumps(open_input, ensure_ascii=False), "status": "running"}})
                    open_result = await asyncio.to_thread(browser_open.invoke, open_input)
                    record_tool_event(tool="browser_open", category="system", thread_id=thread_id or "", mode="local",
                                      input_preview=json.dumps(open_input, ensure_ascii=False), output_preview=str(open_result)[:1000], success=True, source="intent-browser-local")
                    yield json.dumps({"type": "tool_result", "data": {"tool": "browser_open", "status": "completed", "output": str(open_result)[:500]}})
                    yield json.dumps({"type": "token", "content": f"已打开：{url}"})
                    yield json.dumps({"type": "done"})
                    return
                finally:
                    clear_runtime_context(runtime_token)

        # intent == "general" or shortcut failed → fall through to full LLM agent

        if thread_id:
            set_thread_context(thread_id)
            set_local_thread_context(thread_id)

        runtime_token = set_runtime_context(thread_id=thread_id or "", mode="local")

        try:
            memory_context = ""
            if not _should_ignore_memory(message):
                memory_context = await memory_store.get_context_for_query(message)
                try:
                    from app.memory.layered_store import layered_memory
                    layered_context = layered_memory.build_context(message, agent_type="local_agent", session_id=thread_id or "")
                    if layered_context:
                        memory_context = f"{memory_context}\n\n{layered_context}".strip()
                except Exception as e:
                    logger.debug("Suppressed error in agent: %s", e)

            system_content = LOCAL_MODE_SYSTEM + MEMORY_RECALL_GUARDRAILS
            if memory_context:
                system_content += f"\n\nUser context:\n{memory_context}"
            editor_context = build_editor_context_prompt(thread_id or "")
            if editor_context:
                system_content += f"\n\n{editor_context}"

            effective_tools = list(tools or [])
            if "browser_open" in effective_tools and "screenshot" in effective_tools and "browser_open_and_screenshot" not in effective_tools:
                effective_tools.append("browser_open_and_screenshot")
            combined_tools = _filter_tools(
                get_all_tools(include_deferred=False, enable_tool_search=True) + [wrap_langchain_tool(tool_obj) for tool_obj in LOCAL_TOOLS],
                effective_tools or tools,
                disabled_tools,
            )

            raw_msgs = [{"role": m.role, "content": m.content} for m in thread_messages]
            context_summary = None
            if should_summarize(raw_msgs, threshold=10):
                context_summary = await summarize_messages(raw_msgs)
            context_msgs = get_messages_for_context(raw_msgs, context_summary, recent_count=4)

            lc_messages = [SystemMessage(content=system_content)]
            for m in context_msgs:
                if m["role"] == "user":
                    lc_messages.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    lc_messages.append(AIMessage(content=m["content"]))
                elif m["role"] == "system" and context_summary:
                    lc_messages.append(SystemMessage(content=m["content"]))

            lc_messages.append(HumanMessage(content=message))

            # Extract previously modified files from conversation history
            prev_modified = []
            for m in thread_messages:
                if m.role == "assistant" and m.content:
                    for marker in ("Edited ", "Wrote "):
                        if marker in m.content:
                            for token in m.content.split():
                                if "/" in token and not token.startswith("http"):
                                    clean = token.strip("`\"'()[]{},:;")
                                    if clean and clean not in prev_modified:
                                        prev_modified.append(clean)
            if prev_modified:
                lc_messages.append(SystemMessage(content=f"FILES MODIFIED IN THIS CONVERSATION (you can undo with local_undo_edit):\n" + "\n".join(f"  • {f}" for f in prev_modified[-20:])))

            local_clients = local_gateway.list_clients()
            if local_clients:
                lc_messages.append(SystemMessage(content=(
                    f"LOCAL CLIENT CONNECTED: {local_clients[0].get('info', {}).get('hostname', 'unknown')} "
                    f"({local_clients[0].get('info', {}).get('os', 'unknown')}). "
                    f"You ARE connected to this computer. Use local_get_system_info or other local_* tools to demonstrate."
                )))

            full_content = ""
            tool_calls_made = []
            modified_files: list[str] = []
            from app.local.tools import set_stream_queue
            stream_q: asyncio.Queue = asyncio.Queue(maxsize=1000)
            set_stream_queue(stream_q)
            attempt_models = llm_provider.get_fallback_model_names(model)
            for attempt_index, attempt_model in enumerate(attempt_models):
                chat_model = llm_provider.get_chat_model(attempt_model, streaming=True)
                agent = create_react_agent(chat_model, combined_tools)
                try:
                    _LLM_STREAM_TIMEOUT = 180  # 3 min max for entire LLM interaction
                    stream_deadline = asyncio.get_event_loop().time() + _LLM_STREAM_TIMEOUT
                    async for event in agent.astream_events({"messages": lc_messages}, version="v2"):
                        if asyncio.get_event_loop().time() > stream_deadline:
                            logger.warning("LLM stream exceeded %ds timeout for model %s", _LLM_STREAM_TIMEOUT, attempt_model)
                            yield json.dumps({"type": "error", "content": f"模型响应超时（{_LLM_STREAM_TIMEOUT}秒），请重试。"})
                            break
                        kind = event.get("event", "")

                        if kind == "on_chat_model_stream":
                            chunk = event.get("data", {}).get("chunk")
                            if chunk and hasattr(chunk, "content") and chunk.content:
                                if isinstance(chunk.content, str):
                                    full_content += chunk.content
                                    yield json.dumps({"type": "token", "content": chunk.content})

                        elif kind == "on_tool_start":
                            tool_name = event.get("name", "unknown")
                            tool_input = event.get("data", {}).get("input", {})
                            tool_calls_made.append(tool_name)
                            is_local = tool_name.startswith("local_")
                            if tool_name in ("local_edit_file", "local_write_file") and isinstance(tool_input, dict):
                                fpath = tool_input.get("path", "")
                                if fpath and fpath not in modified_files:
                                    modified_files.append(fpath)
                            yield json.dumps({
                                "type": "tool_call",
                                "data": {
                                    "tool": tool_name,
                                    "input": str(tool_input)[:200],
                                    "status": "running",
                                    "local": is_local,
                                },
                            })

                        elif kind == "on_tool_end":
                            tool_name = event.get("name", "unknown")
                            output = event.get("data", {}).get("output", "")
                            is_local = tool_name.startswith("local_")
                            # Flush any real-time stream chunks accumulated during tool execution
                            while not stream_q.empty():
                                try:
                                    chunk = stream_q.get_nowait()
                                    yield json.dumps({"type": "stream_output", "data": chunk})
                                except Exception as e:
                                    logger.debug("Suppressed error in agent: %s", e)
                                    break
                            yield json.dumps({
                                "type": "tool_result",
                                "data": {
                                    "tool": tool_name,
                                    "output": str(output)[:500],
                                    "status": "completed",
                                    "local": is_local,
                                },
                            })
                            validation_result = extract_validation_result(output)
                            if validation_result:
                                yield json.dumps({
                                    "type": "validation_result",
                                    "data": {
                                        "tool": tool_name,
                                        "local": is_local,
                                        **validation_result,
                                    },
                                })
                            for permission_event in consume_permission_events():
                                yield json.dumps({"type": "permission_decision", "data": permission_event})
                            summary = consume_tool_use_summary()
                            if summary:
                                yield json.dumps({"type": "tool_summary", "data": {"summary": summary}})

                    if not full_content and tool_calls_made:
                        full_content = f"(Used tools: {', '.join(tool_calls_made)})"
                    break
                except Exception as e:
                    error_msg = str(e)
                    if "tool_call" in error_msg.lower() or "function_call" in error_msg.lower():
                        chat_model_no_tools = llm_provider.get_chat_model(attempt_model, streaming=True)
                        try:
                            simple_msgs = [SystemMessage(content=system_content), HumanMessage(content=message)]
                            async for chunk in chat_model_no_tools.astream(simple_msgs):
                                if hasattr(chunk, "content") and chunk.content:
                                    full_content += chunk.content
                                    yield json.dumps({"type": "token", "content": chunk.content})
                            break
                        except Exception as e2:
                            can_retry = (
                                attempt_index < len(attempt_models) - 1
                                and not full_content
                                and not tool_calls_made
                                and llm_provider.should_retry_with_fallback(e2)
                            )
                            if can_retry:
                                logger.warning("Local mode model %s failed in no-tools fallback, retrying with fallback model: %s", attempt_model, e2)
                                continue
                            logger.exception("Local mode model %s failed in no-tools fallback", attempt_model)
                            yield json.dumps({"type": "error", "content": _format_local_error(e2, attempt_model)})
                            break
                        finally:
                            await llm_provider.aclose_model(chat_model_no_tools)
                    can_retry = (
                        attempt_index < len(attempt_models) - 1
                        and not full_content
                        and not tool_calls_made
                        and llm_provider.should_retry_with_fallback(e)
                    )
                    if can_retry:
                        logger.warning("Local mode model %s failed, retrying with fallback model: %s", attempt_model, e)
                        continue
                    logger.exception("Local mode model %s failed", attempt_model)
                    yield json.dumps({"type": "error", "content": _format_local_error(e, attempt_model)})
                    break
                finally:
                    await llm_provider.aclose_model(chat_model)

            if _message_requests_screenshot(message) and not any(name in {"screenshot", "browser_open_and_screenshot"} for name in tool_calls_made) and _is_tool_not_disabled("screenshot", disabled_tools):
                try:
                    from app.agents.system_tools import browser_wait_for_ready, screenshot
                    from app.agents.tool_runtime import record_tool_event

                    wait_input = {"url": "", "browser": "Safari", "timeout": 12}
                    yield json.dumps({"type": "tool_call", "data": {"tool": "browser_wait_for_ready", "input": json.dumps(wait_input, ensure_ascii=False), "status": "running"}})
                    wait_result = await asyncio.to_thread(browser_wait_for_ready.invoke, wait_input)
                    record_tool_event(
                        tool="browser_wait_for_ready",
                        category="system",
                        thread_id=thread_id or "",
                        mode="local",
                        input_preview=json.dumps(wait_input, ensure_ascii=False),
                        output_preview=str(wait_result)[:1000],
                        success=True,
                        source="screenshot-fallback-local",
                    )
                    yield json.dumps({"type": "tool_result", "data": {"tool": "browser_wait_for_ready", "status": "completed", "output": str(wait_result)[:500]}})

                    shot_input = {"region": "full", "wait_for_browser": True, "browser": "Safari", "timeout": 8}
                    yield json.dumps({"type": "tool_call", "data": {"tool": "screenshot", "input": json.dumps(shot_input, ensure_ascii=False), "status": "running"}})
                    shot_result = await asyncio.to_thread(screenshot.invoke, shot_input)
                    record_tool_event(
                        tool="screenshot",
                        category="system",
                        thread_id=thread_id or "",
                        mode="local",
                        input_preview=json.dumps(shot_input, ensure_ascii=False),
                        output_preview=str(shot_result)[:1000],
                        success=True,
                        source="screenshot-fallback-local",
                    )
                    shot_content = _format_screenshot_chat_content(str(shot_result))
                    yield json.dumps({"type": "tool_result", "data": {"tool": "screenshot", "status": "completed", "output": str(shot_result)[:500]}})
                    yield json.dumps({"type": "token", "content": f"\n\n{shot_content}"})
                except Exception as exc:
                    yield json.dumps({"type": "error", "content": f"截图兜底失败：{exc}"})

        except Exception as e:
            logger.exception("Local mode failed")
            yield json.dumps({"type": "error", "content": _format_local_error(e, model)})
        finally:
            set_stream_queue(None)
            clear_runtime_context(runtime_token)

        if modified_files:
            yield json.dumps({"type": "files_changed", "data": {"files": modified_files, "count": len(modified_files)}})

        yield json.dumps({"type": "done"})


local_agent = LocalAgent()
