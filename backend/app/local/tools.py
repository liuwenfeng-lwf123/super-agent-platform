from langchain_core.tools import tool
from app.local.gateway import local_gateway
import contextvars
import logging

logger = logging.getLogger(__name__)

_thread_ctx = contextvars.ContextVar("thread_id", default="_default")


def set_local_thread_context(thread_id: str):
    _thread_ctx.set(thread_id)


def _get_thread_id() -> str:
    return _thread_ctx.get()


async def _call_local(action: str, params: dict) -> dict:
    thread_id = _get_thread_id()
    client = local_gateway.get_client_for_thread(thread_id)
    if not client:
        logger.warning(f"No local client for thread {thread_id}")
        return {"success": False, "error": "No local client connected. Please start the local client on your computer."}

    logger.info(f"Calling local action={action} on client={client.client_id}, thread={thread_id}")
    result = await client.send_request(action, params)
    local_gateway.add_audit(client.client_id, action, params, result)
    logger.info(f"Local action={action} result: success={result.get('success')}")
    return result


@tool
async def local_execute_bash(command: str) -> str:
    """Execute a bash command on the user's LOCAL computer. This runs directly on the user's machine, not in a sandbox.
    Use this to operate the user's computer: run programs, manage files, install software, use git, etc.
    The user will be asked to approve each command before execution."""
    result = await _call_local("execute_bash", {"command": command})
    if result.get("success"):
        output = result.get("output", "")
        if result.get("error"):
            output += f"\n[stderr: {result['error'][:1000]}]"
        return output if output else "(No output)"
    return f"Command failed: {result.get('error', 'Unknown error')}"


@tool
async def local_read_file(path: str) -> str:
    """Read a file from the user's LOCAL computer. The path is on the user's actual filesystem.
    The user will be asked to approve before the file is read."""
    result = await _call_local("read_file", {"path": path})
    if result.get("success"):
        return result.get("content", "")
    return f"Read failed: {result.get('error', 'Unknown error')}"


@tool
async def local_write_file(path: str, content: str) -> str:
    """Write content to a file on the user's LOCAL computer. The path is on the user's actual filesystem.
    Use this to create or modify files on the user's computer. The user will be asked to approve before writing."""
    result = await _call_local("write_file", {"path": path, "content": content})
    if result.get("success"):
        return f"File written: {path} ({result.get('size', '?')} bytes)"
    return f"Write failed: {result.get('error', 'Unknown error')}"


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


LOCAL_TOOLS = [
    local_execute_bash,
    local_read_file,
    local_write_file,
    local_list_files,
    local_execute_python,
    local_open_app,
    local_get_system_info,
]
