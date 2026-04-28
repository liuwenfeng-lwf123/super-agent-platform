"""
LSP-equivalent code intelligence tools (Claude Code pattern).

Provides jump-to-definition, find-references, document-symbols, and
call-hierarchy via Jedi (Python) and tree-sitter-based grep fallback
for non-Python files.

No running language server required — all analysis is static.
"""
import os
import re
import json
import subprocess
from pathlib import Path
from typing import Optional
from langchain_core.tools import tool
from app.agents import static_code_intel
from app.agents import ts_language_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jedi_script(file_path: str, line: int, column: int):
    """Create a Jedi Script for the given file position."""
    import jedi
    source = Path(file_path).read_text(encoding="utf-8", errors="replace")
    return jedi.Script(code=source, path=file_path), source


def _format_names(names, limit: int = 30) -> str:
    """Format a list of Jedi Name objects into readable output."""
    lines = []
    for n in names[:limit]:
        loc = f"{n.module_path}:{n.line}" if n.module_path else f"(builtin):{n.line}"
        desc = n.description[:80] if n.description else n.type
        lines.append(f"  {loc}  {desc}")
    if len(names) > limit:
        lines.append(f"  ... and {len(names) - limit} more")
    return "\n".join(lines) if lines else "  (none found)"


def _grep_symbol(symbol: str, directory: str, extensions: list[str], limit: int = 30) -> str:
    """Fallback: grep for symbol across files when Jedi is unavailable."""
    ext_args = []
    for ext in extensions:
        ext_args.extend(["--include", f"*.{ext}"])
    try:
        result = subprocess.run(
            ["grep", "-rnI", "--color=never"] + ext_args + [symbol, directory],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.strip().split("\n")[:limit]
        return "\n".join(f"  {l}" for l in lines if l.strip()) or "  (none found)"
    except Exception as e:
        return f"  grep error: {e}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def goto_definition(file_path: str, line: int, column: int = 0) -> str:
    """Jump to the definition of the symbol at the given file position.

    Args:
        file_path: Absolute path to the source file
        line: 1-indexed line number
        column: 0-indexed column offset (default: 0)
    """
    if not os.path.isfile(file_path):
        return f"File not found: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    # Python: use Jedi
    if ext == ".py":
        try:
            script, _src = _jedi_script(file_path, line, column)
            defs = script.goto(line=line, column=column)
            if not defs:
                # Try infer as fallback
                defs = script.infer(line=line, column=column)
            if not defs:
                return "No definition found at this position."
            results = []
            for d in defs[:10]:
                loc = f"{d.module_path}:{d.line},{d.column}" if d.module_path else f"(builtin):{d.line}"
                results.append(f"  {d.full_name}  →  {loc}")
            return "**Definitions:**\n" + "\n".join(results)
        except Exception as e:
            return f"Jedi error: {e}"

    if ts_language_service.supports_extension(ext):
        try:
            if ts_language_service.is_available(file_path):
                return ts_language_service.goto_definition(file_path, line, column)
        except Exception as e:
            if not static_code_intel.supports_extension(ext):
                return f"TypeScript service error: {e}"

    if static_code_intel.supports_extension(ext):
        try:
            return static_code_intel.goto_definition(file_path, line, column)
        except Exception as e:
            return f"Static analysis error: {e}"

    # Non-Python: read the line and grep for the symbol
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if line < 1 or line > len(lines):
            return f"Line {line} out of range (file has {len(lines)} lines)"
        target_line = lines[line - 1]
        # Extract word at column
        words = re.findall(r'\b\w+\b', target_line)
        symbol = None
        pos = 0
        for w in words:
            idx = target_line.find(w, pos)
            if idx <= column <= idx + len(w):
                symbol = w
                break
            pos = idx + len(w)
        if not symbol:
            symbol = words[0] if words else None
        if not symbol:
            return "No symbol found at this position."
        directory = os.path.dirname(file_path)
        ext_map = {".js": ["js", "jsx"], ".ts": ["ts", "tsx"], ".go": ["go"], ".rs": ["rs"]}
        exts = ext_map.get(ext, [ext.lstrip(".")])
        matches = _grep_symbol(f"(def |function |class |const |let |var |type ){symbol}", directory, exts)
        return f"**grep definitions for `{symbol}`:**\n{matches}"
    except Exception as e:
        return f"Error: {e}"


@tool
def find_references(file_path: str, line: int, column: int = 0) -> str:
    """Find all references to the symbol at the given file position.

    Args:
        file_path: Absolute path to the source file
        line: 1-indexed line number
        column: 0-indexed column offset (default: 0)
    """
    if not os.path.isfile(file_path):
        return f"File not found: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".py":
        try:
            script, _src = _jedi_script(file_path, line, column)
            refs = script.get_references(line=line, column=column)
            if not refs:
                return "No references found."
            return f"**References ({len(refs)}):**\n" + _format_names(refs)
        except Exception as e:
            return f"Jedi error: {e}"

    if ts_language_service.supports_extension(ext):
        try:
            if ts_language_service.is_available(file_path):
                return ts_language_service.find_references(file_path, line, column)
        except Exception as e:
            if not static_code_intel.supports_extension(ext):
                return f"TypeScript service error: {e}"

    if static_code_intel.supports_extension(ext):
        try:
            return static_code_intel.find_references(file_path, line, column)
        except Exception as e:
            return f"Static analysis error: {e}"

    # Non-Python fallback
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        target_line = lines[line - 1] if 0 < line <= len(lines) else ""
        words = re.findall(r'\b\w+\b', target_line)
        symbol = None
        pos = 0
        for w in words:
            idx = target_line.find(w, pos)
            if idx <= column <= idx + len(w):
                symbol = w
                break
            pos = idx + len(w)
        if not symbol:
            return "No symbol at position."
        directory = os.path.dirname(file_path)
        ext_map = {".js": ["js", "jsx", "ts", "tsx"], ".ts": ["ts", "tsx", "js", "jsx"],
                    ".go": ["go"], ".rs": ["rs"]}
        exts = ext_map.get(ext, [ext.lstrip(".")])
        matches = _grep_symbol(symbol, directory, exts)
        return f"**References for `{symbol}`:**\n{matches}"
    except Exception as e:
        return f"Error: {e}"


@tool
def document_symbols(file_path: str) -> str:
    """List all symbols (functions, classes, variables) defined in a file.

    Args:
        file_path: Absolute path to the source file
    """
    if not os.path.isfile(file_path):
        return f"File not found: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".py":
        try:
            script, _src = _jedi_script(file_path, 1, 0)
            names = script.get_names(all_scopes=False, definitions=True)
            if not names:
                return "No symbols found."
            lines = []
            for n in names[:50]:
                kind = n.type  # function, class, statement, module, param
                lines.append(f"  L{n.line:>4}  {kind:<12}  {n.name}")
            return f"**Symbols in {os.path.basename(file_path)} ({len(names)}):**\n" + "\n".join(lines)
        except Exception as e:
            return f"Jedi error: {e}"

    if ts_language_service.supports_extension(ext):
        try:
            if ts_language_service.is_available(file_path):
                return ts_language_service.document_symbols(file_path)
        except Exception as e:
            if not static_code_intel.supports_extension(ext):
                return f"TypeScript service error: {e}"

    if static_code_intel.supports_extension(ext):
        try:
            return static_code_intel.document_symbols(file_path)
        except Exception as e:
            return f"Static analysis error: {e}"

    # Non-Python: regex-based extraction
    patterns = {
        ".js":  r'^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)',
        ".ts":  r'^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var|type|interface|enum)\s+(\w+)',
        ".tsx": r'^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var|type|interface|enum)\s+(\w+)',
        ".jsx": r'^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)',
        ".go":  r'^(?:func|type|var|const)\s+(\w+)',
        ".rs":  r'^(?:pub\s+)?(?:fn|struct|enum|type|const|static|trait|impl)\s+(\w+)',
    }
    pat = patterns.get(ext)
    if not pat:
        pat = r'^(?:def|class|function|struct|enum|type|interface|const|var|let)\s+(\w+)'

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            file_lines = f.readlines()
        symbols = []
        for i, line_text in enumerate(file_lines, 1):
            m = re.match(pat, line_text.strip())
            if m:
                symbols.append(f"  L{i:>4}  {m.group(1)}")
        if not symbols:
            return "No symbols found."
        return f"**Symbols in {os.path.basename(file_path)} ({len(symbols)}):**\n" + "\n".join(symbols[:50])
    except Exception as e:
        return f"Error: {e}"


@tool
def call_hierarchy(file_path: str, function_name: str) -> str:
    """Show incoming calls (who calls this function) and outgoing calls (what it calls).

    Args:
        file_path: Absolute path to the source file
        function_name: Name of the function to analyze
    """
    if not os.path.isfile(file_path):
        return f"File not found: {file_path}"

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".py":
        try:
            import jedi
            source = Path(file_path).read_text(encoding="utf-8", errors="replace")
            script = jedi.Script(code=source, path=file_path)

            # Find the function definition line
            names = script.get_names(all_scopes=True, definitions=True)
            func_def = None
            for n in names:
                if n.name == function_name and n.type in ("function", "class"):
                    func_def = n
                    break
            if not func_def:
                return f"Function `{function_name}` not found in {file_path}"

            # Incoming: who references this function
            refs = script.get_references(line=func_def.line, column=func_def.column)
            incoming = []
            for r in refs:
                if r.line != func_def.line or str(r.module_path) != str(file_path):
                    loc = f"{r.module_path}:{r.line}" if r.module_path else f"?:{r.line}"
                    incoming.append(f"  ← {loc}")

            # Outgoing: what does this function call (scan function body for names)
            tree_names = script.get_names(all_scopes=True, definitions=False, references=True)
            outgoing_set = set()
            # Find function body range
            func_line = func_def.line
            # Heuristic: scan lines after def until next def or end
            src_lines = source.split("\n")
            body_start = func_line
            body_end = len(src_lines)
            indent = None
            for i in range(func_line, len(src_lines)):
                stripped = src_lines[i]
                if i == func_line - 1:
                    continue
                if indent is None and stripped.strip():
                    indent = len(stripped) - len(stripped.lstrip())
                elif indent is not None and stripped.strip() and (len(stripped) - len(stripped.lstrip())) <= indent and i > func_line:
                    body_end = i
                    break

            for n in tree_names:
                if body_start <= n.line <= body_end and n.type in ("function", "class"):
                    if n.name != function_name:
                        outgoing_set.add(n.name)

            parts = [f"**Call hierarchy for `{function_name}`:**\n"]
            parts.append(f"Incoming ({len(incoming)}):")
            parts.append("\n".join(incoming[:20]) if incoming else "  (none found)")
            parts.append(f"\nOutgoing ({len(outgoing_set)}):")
            parts.append("\n".join(f"  → {n}" for n in sorted(outgoing_set)[:20]) if outgoing_set else "  (none found)")
            return "\n".join(parts)

        except Exception as e:
            return f"Jedi error: {e}"

    if ts_language_service.supports_extension(ext):
        try:
            if ts_language_service.is_available(file_path):
                return ts_language_service.call_hierarchy(file_path, function_name)
        except Exception as e:
            if not static_code_intel.supports_extension(ext):
                return f"TypeScript service error: {e}"

    if static_code_intel.supports_extension(ext):
        try:
            return static_code_intel.call_hierarchy(file_path, function_name)
        except Exception as e:
            return f"Static analysis error: {e}"

    # Non-Python fallback: grep
    directory = os.path.dirname(file_path)
    ext_map = {".js": ["js", "jsx"], ".ts": ["ts", "tsx"], ".go": ["go"], ".rs": ["rs"]}
    exts = ext_map.get(ext, [ext.lstrip(".")])

    incoming = _grep_symbol(f"{function_name}\\s*\\(", directory, exts)
    return f"**Call hierarchy for `{function_name}` (grep):**\n\nIncoming:\n{incoming}"


# All LSP tools
LSP_TOOLS = [goto_definition, find_references, document_symbols, call_hierarchy]
