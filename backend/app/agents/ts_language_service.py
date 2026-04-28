import json
import os
import shutil
import subprocess
from pathlib import Path

SUPPORTED_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"}
_BRIDGE_PATH = Path(__file__).with_name("typescript_bridge.js")


def supports_extension(ext: str) -> bool:
    return ext.lower() in SUPPORTED_EXTENSIONS


def _find_node() -> str | None:
    override = os.environ.get("NODE_BINARY", "").strip()
    if override:
        return override
    return shutil.which("node")


def _find_typescript_lib(file_path: str) -> str | None:
    override = os.environ.get("TYPESCRIPT_LIB_PATH", "").strip()
    if override and os.path.exists(override):
        return override
    search_roots = [Path(file_path).resolve().parent, _BRIDGE_PATH.resolve().parent]
    seen: set[Path] = set()
    for root in search_roots:
        for directory in [root, *root.parents]:
            if directory in seen:
                continue
            seen.add(directory)
            direct = directory / "node_modules" / "typescript" / "lib" / "typescript.js"
            frontend = directory / "frontend" / "node_modules" / "typescript" / "lib" / "typescript.js"
            if direct.exists():
                return str(direct)
            if frontend.exists():
                return str(frontend)
    return None


def is_available(file_path: str) -> bool:
    return bool(_find_node() and _find_typescript_lib(file_path) and _BRIDGE_PATH.exists())


def _invoke(payload: dict, file_path: str) -> dict:
    node = _find_node()
    if not node:
        raise RuntimeError("Node.js is not available")
    ts_lib = _find_typescript_lib(file_path)
    if not ts_lib:
        raise RuntimeError("TypeScript language service is not available")
    env = {**os.environ, "TYPESCRIPT_LIB_PATH": ts_lib}
    proc = subprocess.run(
        [node, str(_BRIDGE_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        cwd=str(Path(file_path).resolve().parent),
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise RuntimeError(stderr)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid TypeScript bridge response: {exc}") from exc
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "TypeScript bridge failed")
    return data


def _format_locations(items: list[dict], prefix: str = "  ", limit: int = 20) -> str:
    lines = []
    for item in items[:limit]:
        location = f"{item['file']}:{item['line']},{item['column']}"
        name = item.get("name", "")
        kind = item.get("kind", "")
        label = f"  {name}" if name else ""
        suffix = f"  [{kind}]" if kind else ""
        lines.append(f"{prefix}{location}{label}{suffix}")
    if len(items) > limit:
        lines.append(f"{prefix}... and {len(items) - limit} more")
    return "\n".join(lines) if lines else f"{prefix}(none found)"


def goto_definition(file_path: str, line: int, column: int = 0) -> str:
    data = _invoke({
        "operation": "goto_definition",
        "file_path": file_path,
        "line": line,
        "column": column,
    }, file_path)
    definitions = data.get("definitions", [])
    if not definitions:
        return "No definition found at this position."
    return "**Definitions (TypeScript service):**\n" + _format_locations(definitions)


def find_references(file_path: str, line: int, column: int = 0) -> str:
    data = _invoke({
        "operation": "find_references",
        "file_path": file_path,
        "line": line,
        "column": column,
    }, file_path)
    references = data.get("references", [])
    if not references:
        return "No references found."
    return f"**References ({len(references)}) via TypeScript service:**\n" + _format_locations(references)


def document_symbols(file_path: str) -> str:
    data = _invoke({
        "operation": "document_symbols",
        "file_path": file_path,
    }, file_path)
    symbols = data.get("symbols", [])
    if not symbols:
        return "No symbols found."
    lines = [f"  L{item['line']:>4}  {item.get('kind', 'symbol'):<12}  {item['name']}" for item in symbols[:60]]
    if len(symbols) > 60:
        lines.append(f"  ... and {len(symbols) - 60} more")
    return f"**Symbols in {os.path.basename(file_path)} ({len(symbols)}) via TypeScript service:**\n" + "\n".join(lines)


def call_hierarchy(file_path: str, function_name: str) -> str:
    data = _invoke({
        "operation": "call_hierarchy",
        "file_path": file_path,
        "symbol_name": function_name,
    }, file_path)
    incoming = data.get("incoming", [])
    outgoing = data.get("outgoing", [])
    parts = [f"**Call hierarchy for `{function_name}` via TypeScript service:**\n"]
    parts.append(f"Incoming ({len(incoming)}):")
    parts.append(_format_locations(incoming, prefix="  ← "))
    parts.append(f"\nOutgoing ({len(outgoing)}):")
    parts.append(_format_locations(outgoing, prefix="  → "))
    return "\n".join(parts)
