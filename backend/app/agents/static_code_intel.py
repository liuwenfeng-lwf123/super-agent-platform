"""Static multi-language code intelligence helpers for JS/TS-like languages.

This module upgrades non-Python code navigation from raw grep to a lightweight
semantic index built from imports, exports, definitions, and call sites.

It is intentionally dependency-free and focuses on the highest-value frontend
languages: JavaScript, TypeScript, JSX, and TSX.
"""
import bisect
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

_JS_TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")
_WORKSPACE_ROOT_MARKERS = ("package.json", "tsconfig.json", "jsconfig.json")
_IGNORED_DIRS = {
    ".git", "node_modules", "dist", "build", "coverage", ".next",
    "target", "venv", ".venv", "__pycache__",
}
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*")
_LITERAL_OR_COMMENT_RE = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`")
_CALL_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "typeof", "await", "new"}


@dataclass(frozen=True)
class StaticSymbol:
    name: str
    kind: str
    file_path: str
    line: int
    column: int
    offset: int
    export_name: Optional[str] = None
    is_default: bool = False


@dataclass(frozen=True)
class ImportBinding:
    alias: str
    imported_name: str
    module_spec: str
    resolved_path: str
    kind: str = "named"


@dataclass(frozen=True)
class ReferenceHit:
    file_path: str
    line: int
    column: int
    text: str


@dataclass(frozen=True)
class StaticModule:
    file_path: str
    source: str
    lines: tuple[str, ...]
    definitions: tuple[StaticSymbol, ...]
    imports: dict[str, ImportBinding]
    exports: dict[str, StaticSymbol]
    re_exports: dict[str, ImportBinding]
    export_all: tuple[str, ...]


def supports_extension(ext: str) -> bool:
    return ext.lower() in _JS_TS_EXTENSIONS


def _normalize_path(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def _build_line_starts(source: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(source):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _offset_to_line_col(line_starts: list[int], offset: int) -> tuple[int, int]:
    line = bisect.bisect_right(line_starts, offset)
    return line, offset - line_starts[line - 1]


def _mask_source(source: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return _LITERAL_OR_COMMENT_RE.sub(_replace, source)


def _symbol_at_position(lines: Iterable[str], line: int, column: int) -> Optional[str]:
    cached_lines = list(lines)
    if line < 1 or line > len(cached_lines):
        return None
    target_line = cached_lines[line - 1]
    for match in _IDENTIFIER_RE.finditer(target_line):
        if match.start() <= column <= match.end():
            return match.group(0)
    return None


def _namespace_alias_at_position(line_text: str, column: int) -> Optional[str]:
    for match in _IDENTIFIER_RE.finditer(line_text):
        if not (match.start() <= column <= match.end()):
            continue
        prefix = line_text[:match.start()].rstrip()
        if not prefix.endswith("."):
            return None
        alias_match = re.search(r"([A-Za-z_$][\w$]*)\s*\.\s*$", prefix)
        return alias_match.group(1) if alias_match else None
    return None


def _find_workspace_root(file_path: str) -> str:
    current = Path(file_path).resolve().parent
    for parent in (current, *current.parents):
        if any((parent / marker).exists() for marker in _WORKSPACE_ROOT_MARKERS):
            return str(parent)
    return str(current)


def _iter_workspace_files(root: str, limit: int = 800) -> Iterable[str]:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _IGNORED_DIRS and not name.startswith(".")]
        for filename in filenames:
            if not supports_extension(os.path.splitext(filename)[1]):
                continue
            yield os.path.join(dirpath, filename)
            count += 1
            if count >= limit:
                return


def _resolve_relative_module(base_file: str, module_spec: str) -> str:
    if not module_spec.startswith("."):
        return ""
    base_dir = os.path.dirname(_normalize_path(base_file))
    raw_path = os.path.normpath(os.path.join(base_dir, module_spec))
    candidates = []
    if os.path.splitext(raw_path)[1]:
        candidates.append(raw_path)
    else:
        for ext in _JS_TS_EXTENSIONS:
            candidates.append(raw_path + ext)
        for ext in _JS_TS_EXTENSIONS:
            candidates.append(os.path.join(raw_path, f"index{ext}"))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return _normalize_path(candidate)
    return ""


def _split_alias_items(raw_items: str) -> list[tuple[str, str]]:
    items = []
    for item in raw_items.split(","):
        cleaned = re.sub(r"^type\s+", "", item.strip())
        if not cleaned:
            continue
        if " as " in cleaned:
            imported, alias = cleaned.split(" as ", 1)
        elif ":" in cleaned:
            imported, alias = cleaned.split(":", 1)
        else:
            imported, alias = cleaned, cleaned
        items.append((imported.strip(), alias.strip()))
    return items


def _consume_import_clause(imports: dict[str, ImportBinding], clause: str, module_spec: str, resolved_path: str):
    cleaned = re.sub(r"^type\s+", "", clause.strip())
    if not cleaned:
        return
    if cleaned.startswith("{") and "}" in cleaned:
        for imported, alias in _split_alias_items(cleaned[1:cleaned.rfind("}")]):
            imports[alias] = ImportBinding(alias=alias, imported_name=imported, module_spec=module_spec, resolved_path=resolved_path, kind="named")
        return
    if cleaned.startswith("* as "):
        alias = cleaned[5:].strip()
        if alias:
            imports[alias] = ImportBinding(alias=alias, imported_name="*", module_spec=module_spec, resolved_path=resolved_path, kind="namespace")
        return
    if "," in cleaned:
        first, rest = cleaned.split(",", 1)
        alias = first.strip()
        if alias:
            imports[alias] = ImportBinding(alias=alias, imported_name="default", module_spec=module_spec, resolved_path=resolved_path, kind="default")
        _consume_import_clause(imports, rest, module_spec, resolved_path)
        return
    alias = cleaned.strip()
    if alias:
        imports[alias] = ImportBinding(alias=alias, imported_name="default", module_spec=module_spec, resolved_path=resolved_path, kind="default")


@lru_cache(maxsize=512)
def _parse_module_cached(file_path: str, mtime_ns: int) -> StaticModule:
    source = Path(file_path).read_text(encoding="utf-8", errors="replace")
    line_starts = _build_line_starts(source)
    definitions: list[StaticSymbol] = []
    imports: dict[str, ImportBinding] = {}
    exports: dict[str, StaticSymbol] = {}
    re_exports: dict[str, ImportBinding] = {}
    export_all: list[str] = []
    local_export_links: dict[str, str] = {}
    patterns = [
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\b", re.MULTILINE)),
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)\b", re.MULTILINE)),
        ("type", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\b", re.MULTILINE)),
        ("interface", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)\b", re.MULTILINE)),
        ("enum", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?enum\s+(?P<name>[A-Za-z_$][\w$]*)\b", re.MULTILINE)),
        ("const", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=", re.MULTILINE)),
    ]
    for kind, pattern in patterns:
        for match in pattern.finditer(source):
            header = match.group(0)
            symbol_kind = kind
            if kind == "const":
                statement = source[match.start(): min(len(source), match.end() + 240)]
                if re.search(r"=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)", statement):
                    symbol_kind = "function"
                else:
                    symbol_kind = "variable"
            line, column = _offset_to_line_col(line_starts, match.start("name"))
            export_name = None
            is_default = "export" in header and "default" in header
            if "export" in header:
                export_name = "default" if is_default else match.group("name")
            symbol = StaticSymbol(
                name=match.group("name"),
                kind=symbol_kind,
                file_path=file_path,
                line=line,
                column=column,
                offset=match.start("name"),
                export_name=export_name,
                is_default=is_default,
            )
            definitions.append(symbol)
            if export_name:
                exports[export_name] = symbol
    definitions.sort(key=lambda item: (item.line, item.column, item.name))
    by_name = {item.name: item for item in definitions}

    for match in re.finditer(r"^\s*import\s+(?P<clause>[^\n;]+?)\s+from\s+[\"\'](?P<module>[^\"\']+)[\"\']", source, re.MULTILINE):
        module_spec = match.group("module")
        resolved_path = _resolve_relative_module(file_path, module_spec)
        _consume_import_clause(imports, match.group("clause"), module_spec, resolved_path)

    for match in re.finditer(r"^\s*const\s*{(?P<names>[^}]+)}\s*=\s*require\(\s*[\"\'](?P<module>[^\"\']+)[\"\']\s*\)", source, re.MULTILINE):
        module_spec = match.group("module")
        resolved_path = _resolve_relative_module(file_path, module_spec)
        for imported, alias in _split_alias_items(match.group("names")):
            imports[alias] = ImportBinding(alias=alias, imported_name=imported, module_spec=module_spec, resolved_path=resolved_path, kind="named")

    for match in re.finditer(r"^\s*const\s+(?P<alias>[A-Za-z_$][\w$]*)\s*=\s*require\(\s*[\"\'](?P<module>[^\"\']+)[\"\']\s*\)", source, re.MULTILINE):
        module_spec = match.group("module")
        resolved_path = _resolve_relative_module(file_path, module_spec)
        alias = match.group("alias")
        imports[alias] = ImportBinding(alias=alias, imported_name="default", module_spec=module_spec, resolved_path=resolved_path, kind="default")

    for match in re.finditer(r"^\s*export\s*{\s*(?P<names>[^}]+)\s*}(?:\s*from\s*[\"\'](?P<module>[^\"\']+)[\"\'])?", source, re.MULTILINE):
        module_spec = match.group("module")
        if module_spec:
            resolved_path = _resolve_relative_module(file_path, module_spec)
            for imported, alias in _split_alias_items(match.group("names")):
                re_exports[alias] = ImportBinding(alias=alias, imported_name=imported, module_spec=module_spec, resolved_path=resolved_path, kind="named")
            continue
        for local_name, alias in _split_alias_items(match.group("names")):
            local_export_links[alias] = local_name

    for match in re.finditer(r"^\s*export\s+default\s+(?P<name>[A-Za-z_$][\w$]*)\b", source, re.MULTILINE):
        local_export_links.setdefault("default", match.group("name"))

    for match in re.finditer(r"^\s*export\s+\*\s+from\s+[\"\'](?P<module>[^\"\']+)[\"\']", source, re.MULTILINE):
        resolved_path = _resolve_relative_module(file_path, match.group("module"))
        if resolved_path:
            export_all.append(resolved_path)

    for export_name, local_name in local_export_links.items():
        if local_name in by_name:
            exports[export_name] = by_name[local_name]

    return StaticModule(
        file_path=file_path,
        source=source,
        lines=tuple(source.splitlines()),
        definitions=tuple(definitions),
        imports=imports,
        exports=exports,
        re_exports=re_exports,
        export_all=tuple(export_all),
    )


def parse_module(file_path: str) -> StaticModule:
    abs_path = _normalize_path(file_path)
    stat = os.stat(abs_path)
    return _parse_module_cached(abs_path, stat.st_mtime_ns)


def _resolve_export(file_path: str, export_name: str, visited: Optional[set[tuple[str, str]]] = None) -> Optional[StaticSymbol]:
    abs_path = _normalize_path(file_path)
    if not os.path.isfile(abs_path):
        return None
    if visited is None:
        visited = set()
    key = (abs_path, export_name)
    if key in visited:
        return None
    visited.add(key)
    parsed = parse_module(abs_path)
    if export_name in parsed.exports:
        return parsed.exports[export_name]
    if export_name in parsed.re_exports:
        return _resolve_import_binding(parsed.re_exports[export_name], visited=visited)
    for export_path in parsed.export_all:
        resolved = _resolve_export(export_path, export_name, visited)
        if resolved is not None:
            return resolved
    return None


def _resolve_import_binding(binding: ImportBinding, member_name: Optional[str] = None, visited: Optional[set[tuple[str, str]]] = None) -> Optional[StaticSymbol]:
    if not binding.resolved_path or not os.path.isfile(binding.resolved_path):
        return None
    if binding.kind == "namespace":
        if member_name:
            return _resolve_export(binding.resolved_path, member_name, visited)
        return None
    return _resolve_export(binding.resolved_path, binding.imported_name, visited)


def _search_workspace_definitions(file_path: str, symbol: str, limit: int = 10) -> list[StaticSymbol]:
    matches: list[StaticSymbol] = []
    seen: set[tuple[str, int, int, str]] = set()
    root = _find_workspace_root(file_path)
    for candidate in _iter_workspace_files(root):
        parsed = parse_module(candidate)
        candidate_matches = []
        if symbol in parsed.exports:
            candidate_matches.append(parsed.exports[symbol])
        candidate_matches.extend(item for item in parsed.definitions if item.name == symbol)
        for item in candidate_matches:
            key = (item.file_path, item.line, item.column, item.name)
            if key in seen:
                continue
            seen.add(key)
            matches.append(item)
            if len(matches) >= limit:
                return matches
    return matches


def resolve_symbol(file_path: str, line: int, column: int) -> tuple[Optional[str], Optional[StaticSymbol]]:
    parsed = parse_module(file_path)
    symbol = _symbol_at_position(parsed.lines, line, column)
    if not symbol:
        return None, None
    line_text = parsed.lines[line - 1] if 0 < line <= len(parsed.lines) else ""
    namespace_alias = _namespace_alias_at_position(line_text, column)
    if namespace_alias and namespace_alias in parsed.imports:
        return symbol, _resolve_import_binding(parsed.imports[namespace_alias], member_name=symbol)
    import_binding = parsed.imports.get(symbol)
    local_def = next((item for item in parsed.definitions if item.name == symbol), None)
    if "import" in line_text and import_binding is not None:
        return symbol, _resolve_import_binding(import_binding)
    if import_binding is not None and local_def is None:
        return symbol, _resolve_import_binding(import_binding)
    if local_def is not None:
        return symbol, local_def
    matches = _search_workspace_definitions(file_path, symbol, limit=1)
    return symbol, matches[0] if matches else None


def _format_definitions(definitions: list[StaticSymbol]) -> str:
    rendered = []
    for item in definitions[:10]:
        export_suffix = f" export={item.export_name}" if item.export_name else ""
        rendered.append(f"  {item.name} ({item.kind})  →  {item.file_path}:{item.line},{item.column}{export_suffix}")
    return "**Definitions:**\n" + "\n".join(rendered)


def _scan_occurrences(source: str, file_path: str, pattern: str, lines: tuple[str, ...], limit: int = 80) -> list[ReferenceHit]:
    masked = _mask_source(source)
    line_starts = _build_line_starts(masked)
    regex = re.compile(pattern)
    hits: list[ReferenceHit] = []
    for match in regex.finditer(masked):
        line, column = _offset_to_line_col(line_starts, match.start())
        text = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        hits.append(ReferenceHit(file_path=file_path, line=line, column=column, text=text))
        if len(hits) >= limit:
            break
    return hits


def find_references_for_symbol(target: StaticSymbol, origin_file: str, limit: int = 80) -> list[ReferenceHit]:
    hits: list[ReferenceHit] = []
    seen: set[tuple[str, int, int]] = set()
    root = _find_workspace_root(origin_file)
    target_export_name = target.export_name or target.name
    target_key = (_normalize_path(target.file_path), target.line, target.column, target.name)

    def _matches_target(candidate: Optional[StaticSymbol]) -> bool:
        if candidate is None:
            return False
        candidate_key = (_normalize_path(candidate.file_path), candidate.line, candidate.column, candidate.name)
        return candidate_key == target_key

    for candidate in _iter_workspace_files(root):
        parsed = parse_module(candidate)
        patterns = []
        if _normalize_path(candidate) == _normalize_path(target.file_path):
            patterns.append(rf"(?<![\w$]){re.escape(target.name)}(?![\w$])")
        for alias, binding in parsed.imports.items():
            if binding.kind == "namespace":
                if _matches_target(_resolve_import_binding(binding, member_name=target_export_name)):
                    patterns.append(rf"(?<![\w$]){re.escape(alias)}\s*\.\s*{re.escape(target_export_name)}(?![\w$])")
                continue
            if _matches_target(_resolve_import_binding(binding)):
                patterns.append(rf"(?<![\w$]){re.escape(alias)}(?![\w$])")
        for export_name, binding in parsed.re_exports.items():
            if _matches_target(_resolve_import_binding(binding)):
                patterns.append(rf"(?<![\w$]){re.escape(export_name)}(?![\w$])")
        for pattern in patterns:
            for hit in _scan_occurrences(parsed.source, parsed.file_path, pattern, parsed.lines, limit=limit):
                key = (hit.file_path, hit.line, hit.column)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(hit)
                if len(hits) >= limit:
                    return sorted(hits, key=lambda item: (item.file_path, item.line, item.column))
    return sorted(hits, key=lambda item: (item.file_path, item.line, item.column))


def _format_reference_hits(symbol: str, hits: list[ReferenceHit]) -> str:
    if not hits:
        return "No references found."
    rendered = [f"  {item.file_path}:{item.line}:{item.column}  {item.text}" for item in hits[:80]]
    return f"**References for `{symbol}` ({len(hits)}):**\n" + "\n".join(rendered)


def _extract_symbol_body(source: str, symbol: StaticSymbol) -> str:
    masked = _mask_source(source)
    search_start = symbol.offset
    arrow_index = masked.find("=>", search_start, min(len(masked), search_start + 300))
    brace_index = masked.find("{", search_start, min(len(masked), search_start + 400))
    if brace_index != -1 and (arrow_index == -1 or brace_index > arrow_index):
        depth = 0
        for index in range(brace_index, len(masked)):
            if masked[index] == "{":
                depth += 1
            elif masked[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[brace_index + 1:index]
    if arrow_index != -1:
        line_end = source.find("\n", arrow_index)
        if line_end == -1:
            line_end = len(source)
        return source[arrow_index + 2:line_end]
    line_end = source.find("\n", search_start)
    if line_end == -1:
        line_end = len(source)
    return source[search_start:line_end]


def _collect_outgoing_calls(source: str, symbol: StaticSymbol) -> list[str]:
    body = _extract_symbol_body(source, symbol)
    masked = _mask_source(body)
    outgoing = []
    seen = set()
    for match in re.finditer(r"(?<![\w$])([A-Za-z_$][\w$]*)\s*\(", masked):
        name = match.group(1)
        if name == symbol.name or name in _CALL_KEYWORDS or name in seen:
            continue
        seen.add(name)
        outgoing.append(name)
    return outgoing


def goto_definition(file_path: str, line: int, column: int) -> str:
    symbol, target = resolve_symbol(file_path, line, column)
    if not symbol:
        return "No symbol found at this position."
    if target is not None:
        return _format_definitions([target])
    matches = _search_workspace_definitions(file_path, symbol, limit=10)
    if matches:
        return _format_definitions(matches)
    return "No definition found at this position."


def find_references(file_path: str, line: int, column: int) -> str:
    symbol, target = resolve_symbol(file_path, line, column)
    if not symbol:
        return "No symbol at position."
    if target is None:
        return f"**References for `{symbol}`:**\n  (unable to resolve canonical definition)"
    hits = find_references_for_symbol(target, file_path)
    return _format_reference_hits(symbol, hits)


def document_symbols(file_path: str) -> str:
    parsed = parse_module(file_path)
    if not parsed.definitions:
        return "No symbols found."
    rendered = [f"  L{item.line:>4}  {item.kind:<12}  {item.name}" for item in parsed.definitions[:80]]
    return f"**Symbols in {os.path.basename(file_path)} ({len(parsed.definitions)}):**\n" + "\n".join(rendered)


def call_hierarchy(file_path: str, function_name: str) -> str:
    parsed = parse_module(file_path)
    target = next((item for item in parsed.definitions if item.name == function_name), None)
    if target is None:
        return f"Function `{function_name}` not found in {file_path}"
    refs = find_references_for_symbol(target, file_path)
    incoming = [item for item in refs if not (_normalize_path(item.file_path) == _normalize_path(target.file_path) and item.line == target.line and item.column == target.column)]
    outgoing = _collect_outgoing_calls(parsed.source, target)
    parts = [f"**Call hierarchy for `{function_name}`:**\n"]
    parts.append(f"Incoming ({len(incoming)}):")
    parts.append("\n".join(f"  ← {item.file_path}:{item.line}:{item.column}" for item in incoming[:20]) if incoming else "  (none found)")
    parts.append(f"\nOutgoing ({len(outgoing)}):")
    parts.append("\n".join(f"  → {item}" for item in outgoing[:20]) if outgoing else "  (none found)")
    return "\n".join(parts)
