from langchain_core.tools import tool
from app.skills.search import web_search_tool
from app.sandbox.manager import sandbox_executor
import contextvars

_thread_ctx = contextvars.ContextVar("thread_id", default="_default")


def set_thread_context(thread_id: str):
    _thread_ctx.set(thread_id)


def _get_current_thread_id() -> str:
    return _thread_ctx.get()


@tool
async def web_search(query: str) -> str:
    """Search the internet for information. Use this when you need to find current, real-time information."""
    results = await web_search_tool.search_and_summarize(query)
    return results


@tool
async def web_fetch(url: str) -> str:
    """Fetch and extract text content from a web page URL. Use this to read articles, documentation, or any web page."""
    result = await web_search_tool.fetch_url(url)
    return result


@tool
async def execute_python(code: str) -> str:
    """Execute Python code and return the output. The code runs in a workspace with file access.
    You can install packages, create files, and run data processing scripts."""
    result = await sandbox_executor.execute_python(code, thread_id=_get_current_thread_id())
    if result["success"]:
        output = result["output"]
        if result["error"]:
            output += f"\n[stderr: {result['error'][:500]}]"
        return output if output else "(No output)"
    return f"Execution failed:\n{result['error']}"


@tool
async def execute_javascript(code: str) -> str:
    """Execute JavaScript code and return the output."""
    result = await sandbox_executor.execute_javascript(code, thread_id=_get_current_thread_id())
    if result["success"]:
        output = result["output"]
        if result["error"]:
            output += f"\n[stderr: {result['error'][:500]}]"
        return output if output else "(No output)"
    return f"Execution failed:\n{result['error']}"


@tool
async def execute_bash(command: str) -> str:
    """Execute a bash shell command. Use for installing packages, running build tools, git operations, etc."""
    result = await sandbox_executor.execute_bash(command, thread_id=_get_current_thread_id())
    if result["success"]:
        output = result["output"]
        if result["error"]:
            output += f"\n[stderr: {result['error'][:1000]}]"
        return output if output else "(No output)"
    return f"Command failed (exit {result['exit_code']}):\n{result['error']}"


@tool
async def write_file(path: str, content: str) -> str:
    """Write content to a file in the workspace. Creates directories as needed.
    Use this to create code files, reports, HTML pages, etc."""
    result = await sandbox_executor.write_file(path, content, thread_id=_get_current_thread_id())
    if result["success"]:
        return f"File written: {path} ({result['size']} bytes)"
    return f"Write failed: {result['error']}"


@tool
async def read_file(path: str) -> str:
    """Read the content of a file from the workspace."""
    result = await sandbox_executor.read_file(path, thread_id=_get_current_thread_id())
    if result["success"]:
        return result["content"]
    return f"Read failed: {result['error']}"


@tool
async def list_files(path: str = ".") -> str:
    """List files and directories in the workspace."""
    result = await sandbox_executor.list_files(path, thread_id=_get_current_thread_id())
    if result["success"]:
        if not result["entries"]:
            return f"Directory '{path}' is empty"
        lines = [f"Contents of {path}/:"]
        for e in result["entries"]:
            prefix = "[DIR]" if e["is_dir"] else "     "
            lines.append(f"  {prefix} {e['name']}")
        return "\n".join(lines)
    return f"List failed: {result['error']}"


@tool
def get_current_time() -> str:
    """Get the current date and time."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def calculate(expression: str) -> str:
    """Evaluate a mathematical expression safely."""
    import ast
    import operator
    ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg,
    }
    try:
        node = ast.parse(expression, mode='eval')
        def _eval(n):
            if isinstance(n, ast.Num): return n.n
            if isinstance(n, ast.Constant): return n.value
            if isinstance(n, ast.BinOp): return ops[type(n.op)](_eval(n.left), _eval(n.right))
            if isinstance(n, ast.UnaryOp): return ops[type(n.op)](_eval(n.operand))
            raise ValueError(f"Unsupported: {type(n)}")
        return str(_eval(node.body))
    except Exception as e:
        return f"Calculation error: {e}"


ALL_TOOLS = [web_search, web_fetch, execute_python, execute_javascript, execute_bash, write_file, read_file, list_files, get_current_time, calculate]
