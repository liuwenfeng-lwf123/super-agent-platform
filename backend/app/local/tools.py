from langchain_core.tools import tool
from app.local.gateway import local_gateway
import asyncio
import contextvars
import logging

logger = logging.getLogger(__name__)

_thread_ctx = contextvars.ContextVar("thread_id", default="_default")
# Module-level fallback for when contextvars don't propagate through
# LangGraph's internal async task boundaries.
_thread_id_fallback: str = "_default"

# Stream output queue: per-request via contextvars (safe for concurrent requests)
_stream_queue_ctx: contextvars.ContextVar[asyncio.Queue | None] = contextvars.ContextVar("stream_queue", default=None)


def set_stream_queue(q: asyncio.Queue | None):
    _stream_queue_ctx.set(q)


def set_local_thread_context(thread_id: str):
    global _thread_id_fallback
    _thread_ctx.set(thread_id)
    _thread_id_fallback = thread_id


def _get_thread_id() -> str:
    val = _thread_ctx.get()
    if val == "_default" and _thread_id_fallback != "_default":
        return _thread_id_fallback
    return val


async def _call_local(action: str, params: dict, on_stream_chunk=None) -> dict:
    thread_id = _get_thread_id()
    client = local_gateway.get_client_for_thread(thread_id)
    if not client:
        logger.warning(f"No local client for thread {thread_id}")
        return {"success": False, "error": "No local client connected. Please start the local client on your computer."}

    logger.info(f"Calling local action={action} on client={client.client_id}, thread={thread_id}")
    if on_stream_chunk:
        result = await client.send_request_streaming(action, params, on_chunk=on_stream_chunk, force_auto_approve=True)
    else:
        result = await client.send_request(action, params, force_auto_approve=True)
    local_gateway.add_audit(client.client_id, action, params, result)
    logger.info(f"Local action={action} result: success={result.get('success')}")
    return result


@tool
async def local_execute_bash(command: str, cwd: str = "", timeout: int = 120) -> str:
    """Execute a bash command on the user's LOCAL computer. This runs directly on the user's machine, not in a sandbox.
    Use this to operate the user's computer: run programs, manage files, install software, use git, etc.
    The user will be asked to approve each command before execution.
    Output is streamed in real-time so you and the user can see progress.
    Args:
        command: The shell command to run.
        cwd: Working directory. If empty, uses the client's default directory.
        timeout: Max seconds to wait (default 120). Use higher for slow builds (e.g. 300).
    Examples:
        - local_execute_bash(command="ls -la")
        - local_execute_bash(command="npm install", cwd="/Users/x/myproject", timeout=300)
    """
    q = _stream_queue_ctx.get(None)

    def _on_chunk(stream_name: str, data: str):
        if q:
            try:
                q.put_nowait({"stream": stream_name, "data": data})
            except Exception as e:
                logger.debug("Suppressed error in tools: %s", e)

    params: dict = {"command": command}
    if cwd:
        params["cwd"] = cwd
    if timeout != 120:
        params["timeout"] = timeout
    result = await _call_local("execute_bash", params, on_stream_chunk=_on_chunk)
    if result.get("success"):
        output = result.get("output", "")
        if result.get("error"):
            output += f"\n[stderr: {result['error'][:1000]}]"
        return output if output else "(No output)"
    return f"Command failed: {result.get('error', 'Unknown error')}"


_SMART_TRUNCATE_LINES = 500
_SMART_TRUNCATE_CHARS = 15000  # ~4K tokens — whichever threshold is hit first triggers smart summary


@tool
async def local_read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """Read a file from the user's LOCAL computer.
    Can read the whole file or a specific line range to save tokens on large files.
    For large files (>500 lines or >15K chars), if no line range is given, returns a smart summary (first 50 + last 20 lines + outline)
    so you can decide which part to read in detail.
    Args:
        path: File path on the user's filesystem
        start_line: First line to read (1-indexed). 0 = read from beginning.
        end_line: Last line to read. 0 = read to end.
    Examples:
        - Read entire file: path="/Users/x/app.py"
        - Read lines 50-100: path="/Users/x/app.py", start_line=50, end_line=100
    """
    params: dict = {"path": path}
    if start_line > 0:
        params["start_line"] = start_line
    if end_line > 0:
        params["end_line"] = end_line
    result = await _call_local("read_file", params)
    if not result.get("success"):
        return f"Read failed: {result.get('error', 'Unknown error')}"

    total = result.get("total_lines", 0)
    showing = result.get("showing", "")
    content = result.get("content", "")

    # If user specified a range, or file is small, return as-is
    is_small = (isinstance(total, int) and total <= _SMART_TRUNCATE_LINES) and len(content) <= _SMART_TRUNCATE_CHARS
    if showing or (start_line > 0) or is_small:
        if showing:
            header = f"[{path} — showing lines {showing} of {total} total]\n"
        else:
            header = f"[{path} — {total} lines]\n"
        return header + content

    # Large file, no range specified → smart summary
    lines = content.split("\n")
    head = lines[:50]
    tail = lines[-20:] if len(lines) > 70 else []

    # Extract structural outline (function/class defs, headings, etc.)
    outline = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if any(stripped.startswith(kw) for kw in ("def ", "class ", "async def ", "function ", "export ", "import ", "from ", "## ", "### ", "# ---")):
            outline.append(f"  L{i}: {stripped[:120]}")
        if len(outline) >= 60:
            outline.append("  ... (outline truncated)")
            break

    parts = [f"[{path} — {total} lines — FILE TOO LARGE, showing smart summary]"]
    parts.append(f"\n=== FIRST 50 LINES ===\n" + "\n".join(f"{i:>6}\t{l}" for i, l in enumerate(head, 1)))
    if tail:
        tail_start = len(lines) - len(tail) + 1
        parts.append(f"\n=== LAST 20 LINES ===\n" + "\n".join(f"{tail_start+i:>6}\t{l}" for i, l in enumerate(tail)))
    if outline:
        parts.append(f"\n=== STRUCTURE OUTLINE ({len(outline)} items) ===\n" + "\n".join(outline))
    parts.append(f"\n💡 Use start_line/end_line to read specific sections, e.g. local_read_file(path=\"{path}\", start_line=100, end_line=200)")
    return "\n".join(parts)


@tool
async def local_write_file(path: str, content: str) -> str:
    """Write content to a file on the user's LOCAL computer. The path is on the user's actual filesystem.
    Use this to create or modify files on the user's computer. The user will be asked to approve before writing."""
    result = await _call_local("write_file", {"path": path, "content": content})
    if result.get("success"):
        return f"File written: {path} ({result.get('size', '?')} bytes)"
    return f"Write failed: {result.get('error', 'Unknown error')}"


@tool
async def local_edit_file(path: str, old_string: str, new_string: str) -> str:
    """Precisely edit a file on the user's LOCAL computer using find-and-replace.
    This is the PREFERRED way to modify code — it only changes the exact part you specify,
    leaving everything else untouched. Much safer than rewriting the whole file.
    Args:
        path: File path on the user's filesystem
        old_string: The exact text to find (must be unique in the file)
        new_string: The replacement text
    Tips:
        - Include enough surrounding context in old_string to make it unique
        - Use local_read_file first to see the current content
        - If old_string matches multiple places, the edit will fail — be more specific
    """
    result = await _call_local("edit_file", {"path": path, "old_string": old_string, "new_string": new_string})
    if result.get("success"):
        line = result.get("line", "?")
        diff = result.get("diff", "")
        msg = f"Edited {path} at line {line}\n"
        if diff:
            msg += f"```diff\n{diff}\n```"
        return msg
    return f"Edit failed: {result.get('error', 'Unknown error')}"


@tool
async def local_undo_edit(path: str) -> str:
    """Undo the most recent edit/write to a file on the user's LOCAL computer.
    Restores from the automatic backup created before each edit.
    Can be called multiple times to undo further back.
    Args:
        path: File path to restore
    """
    result = await _call_local("undo_edit", {"path": path})
    if result.get("success"):
        remaining = result.get("remaining_backups", 0)
        return f"Restored {path} from backup {result.get('restored_from', '?')}. ({remaining} older backup(s) remaining)"
    return f"Undo failed: {result.get('error', 'Unknown error')}"


@tool
async def local_search_code(pattern: str, path: str = ".", include: str = "") -> str:
    """Search for text/regex patterns in files on the user's LOCAL computer using ripgrep/grep.
    Returns matching lines with file paths and line numbers.
    Args:
        pattern: Search pattern (regex supported)
        path: Directory to search in (default: current directory)
        include: File glob filter (e.g. "*.py", "*.ts")
    Examples:
        - Find all TODO comments: pattern="TODO", path="/Users/x/project"
        - Find function definitions: pattern="def handle_", path="/Users/x/project", include="*.py"
    """
    result = await _call_local("search_code", {"pattern": pattern, "path": path, "include": include})
    if result.get("success"):
        matches = result.get("matches", 0)
        output = result.get("output", "")
        if matches == 0:
            return f"No matches found for '{pattern}' in {path}"
        # Structure the output for clarity
        structured = []
        for raw_line in output.strip().split("\n")[:30]:
            parts = raw_line.split(":", 2)
            if len(parts) >= 3:
                structured.append(f"  {parts[0]}:{parts[1]}  {parts[2].strip()}")
            else:
                structured.append(f"  {raw_line}")
        header = f"[{matches} match(es) in {path}]"
        if matches > 30:
            header += f" (showing first 30)"
        return header + "\n" + "\n".join(structured)
    return f"Search failed: {result.get('error', 'Unknown error')}"


@tool
async def local_git(args: str, cwd: str = ".") -> str:
    """Run a git command on the user's LOCAL computer.
    Use this for version control: status, diff, log, commit, branch, push, pull, etc.
    Args:
        args: Git arguments (without 'git' prefix), e.g. "status", "diff --staged", "log -5 --oneline"
        cwd: Working directory (should be inside a git repo)
    Examples:
        - Check status: args="status", cwd="/Users/x/project"
        - View recent commits: args="log -10 --oneline", cwd="/Users/x/project"
        - Create commit: args="commit -am 'fix: resolve login bug'", cwd="/Users/x/project"
        - View diff: args="diff", cwd="/Users/x/project"
        - Create branch: args="checkout -b feature/new-ui", cwd="/Users/x/project"
    """
    result = await _call_local("git_command", {"args": args, "cwd": cwd})
    if result.get("success"):
        output = result.get("output", "")
        return output if output.strip() else "(no output)"
    error = result.get("error", "") or result.get("output", "")
    return f"Git failed: {error}"


@tool
async def local_project_index(path: str = ".") -> str:
    """Analyze and index a project directory on the user's LOCAL computer.
    Returns the file tree structure (up to 4 levels deep) and language statistics.
    Use this FIRST when working with a new codebase to understand its structure.
    Args:
        path: Project root directory
    """
    result = await _call_local("project_index", {"path": path})
    if result.get("success"):
        tree = result.get("tree", "")
        total = result.get("total_files", 0)
        langs = result.get("languages", "")
        return f"[Project: {result.get('path', path)} — {total} files]\nLanguages: {langs}\n\n{tree}"
    return f"Index failed: {result.get('error', 'Unknown error')}"


@tool
async def local_list_files(path: str = ".") -> str:
    """List files and directories on the user's LOCAL computer. The path is on the user's actual filesystem.
    Use this to explore the user's file system. The user will be asked to approve before listing."""
    result = await _call_local("list_files", {"path": path})
    if result.get("success"):
        entries = result.get("entries", [])
        if not entries:
            return f"Directory '{path}' is empty"
        lines = [f"Contents of {path}/:"]
        for e in entries:
            prefix = "[DIR]" if e.get("is_dir") else "     "
            lines.append(f"  {prefix} {e.get('name', '?')}")
        return "\n".join(lines)
    return f"List failed: {result.get('error', 'Unknown error')}"


@tool
async def local_execute_python(code: str) -> str:
    """Execute Python code on the user's LOCAL computer. This runs directly on the user's machine.
    Use this when you need to run Python with the user's local environment, packages, and file access.
    The user will be asked to approve before execution."""
    result = await _call_local("execute_python", {"code": code})
    if result.get("success"):
        output = result.get("output", "")
        if result.get("error"):
            output += f"\n[stderr: {result['error'][:500]}]"
        return output if output else "(No output)"
    return f"Execution failed: {result.get('error', 'Unknown error')}"


@tool
async def local_open_app(app_name: str) -> str:
    """Open an application on the user's LOCAL computer. Examples: 'chrome', 'vscode', 'finder', 'terminal'.
    The user will be asked to approve before the app is opened."""
    result = await _call_local("open_app", {"app_name": app_name})
    if result.get("success"):
        return f"Opened: {app_name}"
    return f"Failed to open: {result.get('error', 'Unknown error')}"


@tool
async def local_get_system_info() -> str:
    """Get system information about the user's LOCAL computer: OS, hostname, disk space, memory, etc.
    This is a read-only operation and is automatically approved."""
    result = await _call_local("get_system_info", {})
    if result.get("success"):
        return result.get("info", "")
    return f"Failed: {result.get('error', 'Unknown error')}"


@tool
async def local_upload_to_workspace(local_path: str, workspace_filename: str) -> str:
    """Transfer a file from the user's LOCAL computer to the AI workspace.
    Use this when you need to analyze, process, or work with a file from the user's computer.
    Supports binary files: images, CSV, Excel, PDF, etc. Max 50MB.
    The user will be asked to approve before the file is read."""
    import base64
    result = await _call_local("upload_file", {"path": local_path})
    if not result.get("success"):
        return f"Upload failed: {result.get('error', 'Unknown error')}"

    from app.runtime_backends import runtime_manager
    thread_id = _get_thread_id()
    data = base64.b64decode(result["data"])
    write_result = await runtime_manager.write_file_bytes(
        workspace_filename, data, thread_id=thread_id
    )
    if write_result.get("success"):
        return f"Uploaded {result.get('filename', local_path)} ({result.get('size', '?')} bytes) to workspace as {workspace_filename}"
    return f"Failed to save to workspace: {write_result.get('error', 'Unknown error')}"


@tool
async def local_download_from_workspace(workspace_path: str, local_path: str) -> str:
    """Transfer a file from the AI workspace to the user's LOCAL computer.
    Use this to deliver generated files (reports, images, code, etc.) to the user's computer.
    The user will be asked to approve before the file is written."""
    import base64
    from app.runtime_backends import runtime_manager
    thread_id = _get_thread_id()
    read_result = await runtime_manager.read_file_bytes(workspace_path, thread_id=thread_id)
    if not read_result.get("success"):
        return f"Failed to read from workspace: {read_result.get('error', 'Unknown error')}"

    encoded = base64.b64encode(read_result["data"]).decode("ascii")
    result = await _call_local("download_file", {"path": local_path, "data": encoded})
    if result.get("success"):
        return f"Downloaded to {result.get('path', local_path)} ({result.get('size', '?')} bytes)"
    return f"Download failed: {result.get('error', 'Unknown error')}"


@tool
async def local_read_clipboard() -> str:
    """Read the current contents of the user's clipboard on their LOCAL computer.
    Returns whatever text the user last copied. Useful for processing clipboard content."""
    result = await _call_local("read_clipboard", {})
    if result.get("success"):
        content = result.get("content", "")
        return content if content else "(clipboard is empty)"
    return f"Failed to read clipboard: {result.get('error', 'Unknown error')}"


@tool
async def local_write_clipboard(content: str) -> str:
    """Write text to the user's clipboard on their LOCAL computer.
    Use this to copy generated text, code, URLs, etc. so the user can paste it elsewhere."""
    result = await _call_local("write_clipboard", {"content": content})
    if result.get("success"):
        return f"Copied {result.get('length', len(content))} characters to clipboard."
    return f"Failed to write clipboard: {result.get('error', 'Unknown error')}"


@tool
async def local_send_notification(title: str, message: str) -> str:
    """Send a desktop notification to the user's LOCAL computer.
    Use this to alert the user when a long task is done, or to deliver a short message.
    Args:
        title: Notification title
        message: Notification body text
    """
    result = await _call_local("send_notification", {"title": title, "message": message})
    if result.get("success"):
        return f"Notification sent: {title}"
    return f"Failed to send notification: {result.get('error', 'Unknown error')}"


@tool
async def local_manage_window(app_name: str, action: str, x: int = 0, y: int = 0, width: int = 800, height: int = 600) -> str:
    """Manage application windows on the user's macOS computer.
    Args:
        app_name: The application name (e.g. "Google Chrome", "Terminal", "Finder")
        action: One of: move, minimize, maximize, close, fullscreen, list
        x: Window X position (only for move)
        y: Window Y position (only for move)
        width: Window width (only for move)
        height: Window height (only for move)
    Examples:
        - Move Chrome to left half: app_name="Google Chrome", action="move", x=0, y=25, width=720, height=875
        - Move WeChat to right half: app_name="WeChat", action="move", x=720, y=25, width=720, height=875
        - Maximize Terminal: app_name="Terminal", action="maximize"
    """
    params = {"x": x, "y": y, "width": width, "height": height}
    result = await _call_local("manage_window", {"app_name": app_name, "action": action, "params": params})
    if result.get("success"):
        if action == "list":
            return f"Windows of {app_name}: {result.get('windows', 'none')}"
        return f"Window action '{action}' applied to {app_name}"
    return f"Window management failed: {result.get('error', 'Unknown error')}"


@tool
async def local_create_schedule(message: str, interval_minutes: int = 0, run_at: str = "") -> str:
    """Create a scheduled/recurring task on the user's computer.
    Use this when the user asks to be reminded periodically or at a specific time.
    Args:
        message: What to remind/notify the user about (e.g. "该喝水了", "检查磁盘空间")
        interval_minutes: Repeat every N minutes (e.g. 30 for every 30 min). Set 0 if one-time.
        run_at: ISO datetime for one-time task (e.g. "2025-01-15T09:00:00"). Leave empty for recurring.
    Examples:
        - "每30分钟提醒我喝水": message="该喝水了", interval_minutes=30
        - "明天9点提醒我开会": message="开会", run_at="2025-01-15T09:00:00"
    """
    from app.local.scheduler import add_schedule
    data: dict = {"message": message}
    if interval_minutes > 0:
        data["interval_minutes"] = interval_minutes
    elif run_at:
        data["run_at"] = run_at
    else:
        return "请指定间隔分钟数或具体时间。"
    result = add_schedule(**data)
    if interval_minutes > 0:
        return f"已创建定时任务：每 {interval_minutes} 分钟提醒「{message}」(ID: {result['id']})"
    return f"已创建定时任务：在 {run_at} 提醒「{message}」(ID: {result['id']})"


@tool
async def local_list_schedules() -> str:
    """List all scheduled tasks on the user's computer."""
    from app.local.scheduler import list_schedules
    schedules = list_schedules()
    if not schedules:
        return "当前没有定时任务。"
    lines = []
    for s in schedules:
        status = "启用" if s.get("enabled") else "停用"
        if s.get("interval_minutes"):
            freq = f"每 {s['interval_minutes']} 分钟"
        elif s.get("run_at"):
            freq = f"一次性：{s['run_at']}"
        else:
            freq = "未设定"
        lines.append(f"- [{status}] {s['message']} ({freq}) ID:{s['id']}")
    return "定时任务列表：\n" + "\n".join(lines)


@tool
async def local_delete_schedule(schedule_id: str) -> str:
    """Delete a scheduled task by its ID."""
    from app.local.scheduler import remove_schedule
    if remove_schedule(schedule_id):
        return f"已删除定时任务 {schedule_id}"
    return f"找不到 ID 为 {schedule_id} 的定时任务"


LOCAL_TOOLS = [
    local_execute_bash,
    local_read_file,
    local_write_file,
    local_edit_file,
    local_undo_edit,
    local_list_files,
    local_execute_python,
    local_open_app,
    local_get_system_info,
    local_upload_to_workspace,
    local_download_from_workspace,
    local_read_clipboard,
    local_write_clipboard,
    local_send_notification,
    local_manage_window,
    local_search_code,
    local_git,
    local_project_index,
    local_create_schedule,
    local_list_schedules,
    local_delete_schedule,
]
