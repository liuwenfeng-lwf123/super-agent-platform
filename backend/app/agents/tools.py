from langchain_core.tools import tool
from app.skills.search import web_search_tool
from app.runtime_backends import runtime_manager
from app.agents.evolution import EVOLUTION_TOOLS, tool_registry
from app.local.editor_state import editor_state_store
from app.agents.system_tools import SYSTEM_TOOLS
from app.agents.lsp_tools import LSP_TOOLS
from app.agents.tool_runtime import (
    can_use_discovered_tool,
    is_tool_search_enabled,
    search_tool_catalog,
    should_defer_tool,
    wrap_langchain_tool,
)
import ast
import contextvars
import json
import shlex
import tomllib

_thread_ctx = contextvars.ContextVar("thread_id", default="_default")
_thread_id_fallback: str = "_default"
_tool_filter_ctx = contextvars.ContextVar("tool_filter", default=None)


def _format_runtime_failure(prefix: str, result: dict) -> str:
    error = str(result.get("error") or "Unknown runtime error")
    hint = str(result.get("hint") or "").strip()
    message = f"{prefix}: {error}"
    if hint:
        message += f"\nHint: {hint}"
    return message


def extract_validation_result(output: object) -> dict[str, str] | None:
    text = str(output or "")
    strategy_hint = _extract_validation_strategy(text)
    latest: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("Validation failed:"):
            latest = {"status": "failed", "message": line}
        elif line.startswith("Validation skipped:"):
            latest = {"status": "skipped", "message": line}
        elif line.startswith("Validation:"):
            latest = {"status": "passed", "message": line}
        if latest and latest.get("message") == line:
            strategy = _extract_strategy_from_validation_line(line) or strategy_hint
            if strategy:
                latest["strategy"] = strategy
    return latest


def _is_validation_strategy_token(value: str) -> bool:
    return bool(value) and all(ch.isalnum() or ch in "._:-" for ch in value)


def _extract_strategy_from_validation_line(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("Validation failed:") and " check failed (" in stripped and "):" in stripped:
        strategy = stripped.split(" check failed (", 1)[1].split("):", 1)[0].strip()
        return strategy if _is_validation_strategy_token(strategy) else None
    if (stripped.startswith("Validation:") or stripped.startswith("Validation skipped:")) and stripped.endswith(")") and " (" in stripped:
        strategy = stripped.rsplit(" (", 1)[1][:-1].strip()
        return strategy if _is_validation_strategy_token(strategy) else None
    return None


def _extract_validation_strategy(text: object) -> str | None:
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("__VALIDATION_STRATEGY__:"):
            strategy = line.split(":", 1)[1].strip()
            return strategy or None
    return None


def _validation_strategy_from_result(result: dict) -> str | None:
    combined = "\n".join(str(part or "") for part in (result.get("output"), result.get("error")) if part)
    return _extract_validation_strategy(combined)


def _strip_validation_strategy_markers(text: object) -> str:
    cleaned = [
        raw_line
        for raw_line in str(text or "").splitlines()
        if not raw_line.strip().startswith("__VALIDATION_STRATEGY__:")
    ]
    return "\n".join(cleaned).strip()


def _with_validation_strategy(message: str, strategy: str | None) -> str:
    return f"{message} ({strategy})" if strategy else message


def _inline_post_write_validation(path: str, content: str) -> str:
    normalized = path.strip().lower()
    if normalized.endswith(".py"):
        try:
            compile(content, path, "exec")
        except SyntaxError as exc:
            line = exc.lineno or "?"
            return f"Validation failed: Python syntax error at line {line}: {exc.msg}"
        except Exception as exc:
            return f"Validation failed: Python syntax check error: {exc}"
        return "Validation: Python syntax OK"
    if normalized.endswith(".json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            return f"Validation failed: JSON parse error at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        except Exception as exc:
            return f"Validation failed: JSON validation error: {exc}"
        return "Validation: JSON parse OK"
    if normalized.endswith(".toml"):
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            return f"Validation failed: TOML parse error: {exc}"
        except Exception as exc:
            return f"Validation failed: TOML validation error: {exc}"
        return "Validation: TOML parse OK"
    return ""


def _is_python_test_path(path: str) -> bool:
    normalized = path.strip().lower()
    basename = normalized.rsplit("/", 1)[-1]
    return normalized.endswith(".py") and (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or basename.startswith("test_")
        or basename.endswith("_test.py")
    )


def _is_probable_node_source_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/").lower()
    source_roots = ("src/", "app/", "pages/", "components/", "lib/", "hooks/")
    source_segments = ("/src/", "/app/", "/pages/", "/components/", "/lib/", "/hooks/")
    return normalized.startswith(source_roots) or any(segment in normalized for segment in source_segments)


def _split_node_source_root(path: str) -> tuple[str, str] | None:
    normalized = path.strip().replace("\\", "/").lstrip("./")
    roots = ("src", "app", "pages", "components", "lib", "hooks")
    best: tuple[int, str, str] | None = None
    for root in roots:
        prefix = ""
        relative = ""
        position = normalized.find(f"/{root}/")
        if normalized.startswith(f"{root}/"):
            relative = normalized[len(root) + 1:]
            position = 0
        elif position != -1:
            prefix = normalized[:position].rstrip("/")
            relative = normalized[position + len(root) + 2:]
        if not relative:
            continue
        if best is None or position < best[0]:
            best = (position, prefix, relative)
    if best is None:
        return None
    return best[1], best[2]


def _related_node_test_candidates(path: str) -> list[str]:
    normalized = path.strip().replace("\\", "/").lstrip("./")
    filename = normalized.rsplit("/", 1)[-1]
    if "." not in filename:
        return []
    lowered_filename = filename.lower()
    if lowered_filename.endswith((
        ".test.ts",
        ".spec.ts",
        ".test.tsx",
        ".spec.tsx",
        ".test.js",
        ".spec.js",
        ".test.jsx",
        ".spec.jsx",
    )):
        return [normalized]
    stem = filename.rsplit(".", 1)[0]
    parent = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
    lowered = normalized.lower()
    if lowered.endswith(".tsx"):
        suffixes = (".test.tsx", ".spec.tsx", ".test.ts", ".spec.ts", ".test.jsx", ".spec.jsx", ".test.js", ".spec.js")
    elif lowered.endswith(".ts"):
        suffixes = (".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx", ".test.js", ".spec.js", ".test.jsx", ".spec.jsx")
    elif lowered.endswith(".jsx"):
        suffixes = (".test.jsx", ".spec.jsx", ".test.js", ".spec.js", ".test.tsx", ".spec.tsx", ".test.ts", ".spec.ts")
    else:
        suffixes = (".test.js", ".spec.js", ".test.jsx", ".spec.jsx", ".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")
    directories = [parent]
    for nested in ("__tests__", "tests"):
        directories.append(f"{parent}/{nested}" if parent else nested)
    source_root = _split_node_source_root(normalized)
    if source_root is not None:
        project_root, relative_path = source_root
        relative_parent = relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""
        for nested in ("tests", "test", "__tests__"):
            directories.append("/".join(part for part in (project_root, nested, relative_parent) if part))
    deduped: list[str] = []
    for directory in directories:
        for suffix in suffixes:
            candidate = "/".join(part for part in (directory, f"{stem}{suffix}") if part)
            if candidate and candidate not in deduped:
                deduped.append(candidate)
    return deduped


def _python_unittest_command(target_path: str, *, shell_expand: bool = False) -> str:
    target_value = target_path if shell_expand else shlex.quote(target_path)
    return (
        "echo '__VALIDATION_STRATEGY__:unittest'\n"
        "TARGET_PATH=" + target_value + " python3 - <<'PY'\n"
        "import importlib.util\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "import unittest\n"
        "path = pathlib.Path(os.environ['TARGET_PATH'])\n"
        "if not path.exists():\n"
        "    raise SystemExit(2)\n"
        "sys.path.insert(0, str(pathlib.Path('.').resolve()))\n"
        "spec = importlib.util.spec_from_file_location(path.stem, path)\n"
        "if spec is None or spec.loader is None:\n"
        "    raise SystemExit(2)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "suite = unittest.defaultTestLoader.loadTestsFromModule(module)\n"
        "if suite.countTestCases() == 0:\n"
        "    raise SystemExit(126)\n"
        "result = unittest.TextTestRunner(verbosity=0).run(suite)\n"
        "raise SystemExit(0 if result.wasSuccessful() else 1)\n"
        "PY"
    )


def _python_pytest_command(target_path: str, *, shell_expand: bool = False) -> str:
    target_value = target_path if shell_expand else shlex.quote(target_path)
    return (
        "echo '__VALIDATION_STRATEGY__:pytest'\n"
        "TARGET_PATH=" + target_value + "\n"
        "python3 - <<'PY' >/dev/null 2>&1\n"
        "import importlib.util\n"
        "raise SystemExit(0 if importlib.util.find_spec('pytest') else 127)\n"
        "PY\n"
        "STATUS=$?\n"
        "if [ \"$STATUS\" -ne 0 ]; then exit \"$STATUS\"; fi\n"
        "python3 -m pytest -q \"$TARGET_PATH\"\n"
        "STATUS=$?\n"
        "if [ \"$STATUS\" -eq 5 ]; then exit 126; fi\n"
        "exit \"$STATUS\""
    )


def _python_repo_test_command(target_path: str, *, shell_expand: bool = False) -> str:
    target_value = target_path if shell_expand else shlex.quote(target_path)
    return (
        "TARGET_PATH=" + target_value + "\n"
        "SEARCH_DIR=$(dirname \"$TARGET_PATH\")\n"
        "USE_PYTEST=0\n"
        "while true; do\n"
        "  if [ -f \"$SEARCH_DIR/pytest.ini\" ] || [ -f \"$SEARCH_DIR/conftest.py\" ]; then USE_PYTEST=1; break; fi\n"
        "  if [ -f \"$SEARCH_DIR/pyproject.toml\" ]; then\n"
        "    TARGET_PYPROJECT=\"$SEARCH_DIR/pyproject.toml\" python3 - <<'PY' >/dev/null 2>&1\n"
        "import os\n"
        "import pathlib\n"
        "text = pathlib.Path(os.environ['TARGET_PYPROJECT']).read_text(encoding='utf-8', errors='ignore')\n"
        "raise SystemExit(0 if '[tool.pytest.ini_options]' in text else 1)\n"
        "PY\n"
        "    STATUS=$?\n"
        "    if [ \"$STATUS\" -eq 0 ]; then USE_PYTEST=1; break; fi\n"
        "  fi\n"
        "  if [ \"$SEARCH_DIR\" = \".\" ] || [ \"$SEARCH_DIR\" = \"/\" ]; then break; fi\n"
        "  NEXT_DIR=$(dirname \"$SEARCH_DIR\")\n"
        "  if [ \"$NEXT_DIR\" = \"$SEARCH_DIR\" ]; then break; fi\n"
        "  SEARCH_DIR=\"$NEXT_DIR\"\n"
        "done\n"
        "if [ \"$USE_PYTEST\" -eq 1 ]; then\n"
        + _python_pytest_command("$TARGET_PATH", shell_expand=True)
        + "\nelse\n"
        + _python_unittest_command("$TARGET_PATH", shell_expand=True)
        + "\nfi"
    )


def _related_python_test_candidates(path: str) -> list[str]:
    normalized = path.strip().replace("\\", "/")
    stem = normalized.rsplit("/", 1)[-1][:-3]
    parent = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
    candidates = [
        f"tests/test_{stem}.py",
        f"tests/{stem}_test.py",
        f"test_{stem}.py",
        f"{stem}_test.py",
    ]
    if parent:
        candidates.extend([
            f"{parent}/test_{stem}.py",
            f"{parent}/{stem}_test.py",
        ])
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _related_python_test_validation_command(path: str) -> str:
    candidates = _related_python_test_candidates(path)
    candidate_block = " ".join(shlex.quote(candidate) for candidate in candidates)
    return (
        "TARGET=\"\"\n"
        f"for CANDIDATE in {candidate_block}; do\n"
        "  if [ -f \"$CANDIDATE\" ]; then TARGET=\"$CANDIDATE\"; break; fi\n"
        "done\n"
        "if [ -z \"$TARGET\" ]; then exit 124; fi\n"
        + _python_repo_test_command("$TARGET", shell_expand=True)
    )


def _typescript_project_validation_command(target_path: str, *, shell_expand: bool = False) -> str:
    target_value = target_path if shell_expand else shlex.quote(target_path)
    return (
        "echo '__VALIDATION_STRATEGY__:tsc'\n"
        "TARGET_PATH=" + target_value + "\n"
        "TARGET_DIR=$(dirname \"$TARGET_PATH\")\n"
        "CFG=\"\"\n"
        "SEARCH_DIR=\"$TARGET_DIR\"\n"
        "while true; do\n"
        "  if [ -f \"$SEARCH_DIR/tsconfig.json\" ]; then CFG=\"$SEARCH_DIR/tsconfig.json\"; break; fi\n"
        "  if [ \"$SEARCH_DIR\" = \".\" ] || [ \"$SEARCH_DIR\" = \"/\" ]; then break; fi\n"
        "  NEXT_DIR=$(dirname \"$SEARCH_DIR\")\n"
        "  if [ \"$NEXT_DIR\" = \"$SEARCH_DIR\" ]; then break; fi\n"
        "  SEARCH_DIR=\"$NEXT_DIR\"\n"
        "done\n"
        "if [ -z \"$CFG\" ] && [ -f \"tsconfig.json\" ]; then CFG=\"tsconfig.json\"; fi\n"
        "if [ -z \"$CFG\" ]; then exit 126; fi\n"
        "CFG_DIR=$(dirname \"$CFG\")\n"
        "if [ -x \"$CFG_DIR/node_modules/.bin/tsc\" ]; then\n"
        "  \"$CFG_DIR/node_modules/.bin/tsc\" --noEmit --pretty false --skipLibCheck -p \"$CFG\"\n"
        "elif [ -x \"./node_modules/.bin/tsc\" ]; then\n"
        "  ./node_modules/.bin/tsc --noEmit --pretty false --skipLibCheck -p \"$CFG\"\n"
        "elif command -v tsc >/dev/null 2>&1; then\n"
        "  tsc --noEmit --pretty false --skipLibCheck -p \"$CFG\"\n"
        "else\n"
        "  exit 127\n"
        "fi"
    )


def _node_project_script_validation_command(
    target_path: str,
    *,
    preferred_scripts: tuple[str, ...] = ("typecheck", "build"),
    shell_expand: bool = False,
) -> str:
    target_value = target_path if shell_expand else shlex.quote(target_path)
    script_tuple = "(" + ", ".join(repr(name) for name in preferred_scripts) + ("," if len(preferred_scripts) == 1 else "") + ")"
    return (
        "TARGET_PATH=" + target_value + "\n"
        "TARGET_DIR=$(dirname \"$TARGET_PATH\")\n"
        "PKG=\"\"\n"
        "SEARCH_DIR=\"$TARGET_DIR\"\n"
        "while true; do\n"
        "  if [ -f \"$SEARCH_DIR/package.json\" ]; then PKG=\"$SEARCH_DIR/package.json\"; break; fi\n"
        "  if [ \"$SEARCH_DIR\" = \".\" ] || [ \"$SEARCH_DIR\" = \"/\" ]; then break; fi\n"
        "  NEXT_DIR=$(dirname \"$SEARCH_DIR\")\n"
        "  if [ \"$NEXT_DIR\" = \"$SEARCH_DIR\" ]; then break; fi\n"
        "  SEARCH_DIR=\"$NEXT_DIR\"\n"
        "done\n"
        "if [ -z \"$PKG\" ] && [ -f \"package.json\" ]; then PKG=\"package.json\"; fi\n"
        "if [ -z \"$PKG\" ]; then exit 126; fi\n"
        "SCRIPT_INFO=$(TARGET_PKG=\"$PKG\" python3 - <<'PY'\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        "pkg = pathlib.Path(os.environ['TARGET_PKG'])\n"
        "data = json.loads(pkg.read_text())\n"
        "scripts = data.get('scripts') or {}\n"
        f"for name in {script_tuple}:\n"
        "    value = scripts.get(name)\n"
        "    if not isinstance(value, str) or not value.strip():\n"
        "        continue\n"
        "    lowered_name = name.strip().lower()\n"
        "    lowered_value = value.strip().lower()\n"
        "    if 'vitest' in lowered_name or 'vitest' in lowered_value:\n"
        "        strategy = 'vitest'\n"
        "    elif 'jest' in lowered_name or 'jest' in lowered_value:\n"
        "        strategy = 'jest'\n"
        "    elif lowered_name in {'test', 'test:ci'}:\n"
        "        continue\n"
        "    else:\n"
        "        strategy = name\n"
        "    print(name + '|' + strategy)\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(125)\n"
        "PY\n"
        ")\n"
        "STATUS=$?\n"
        "if [ \"$STATUS\" -eq 125 ]; then exit 125; fi\n"
        "if [ \"$STATUS\" -ne 0 ]; then exit \"$STATUS\"; fi\n"
        "SCRIPT_NAME=${SCRIPT_INFO%%|*}\n"
        "STRATEGY=${SCRIPT_INFO#*|}\n"
        "if [ -z \"$SCRIPT_NAME\" ]; then exit 125; fi\n"
        "if [ -z \"$STRATEGY\" ] || [ \"$STRATEGY\" = \"$SCRIPT_INFO\" ]; then STRATEGY=\"$SCRIPT_NAME\"; fi\n"
        "echo \"__VALIDATION_STRATEGY__:$STRATEGY\"\n"
        "PKG_DIR=$(dirname \"$PKG\")\n"
        "if [ -f \"$PKG_DIR/pnpm-lock.yaml\" ] && command -v pnpm >/dev/null 2>&1; then\n"
        "  (cd \"$PKG_DIR\" && pnpm run \"$SCRIPT_NAME\")\n"
        "elif [ -f \"$PKG_DIR/yarn.lock\" ] && command -v yarn >/dev/null 2>&1; then\n"
        "  if [ \"$SCRIPT_NAME\" = \"build\" ]; then\n"
        "    (cd \"$PKG_DIR\" && yarn build)\n"
        "  else\n"
        "    (cd \"$PKG_DIR\" && yarn \"$SCRIPT_NAME\")\n"
        "  fi\n"
        "elif command -v npm >/dev/null 2>&1; then\n"
        "  (cd \"$PKG_DIR\" && npm run \"$SCRIPT_NAME\")\n"
        "else\n"
        "  exit 127\n"
        "fi"
    )


def _node_related_test_validation_command(
    target_path: str,
    *,
    preferred_scripts: tuple[str, ...] = ("test:unit", "test:unit:ci", "vitest", "jest", "test", "test:ci"),
    shell_expand: bool = False,
) -> str:
    target_value = target_path if shell_expand else shlex.quote(target_path)
    candidates = _related_node_test_candidates(target_path)
    candidate_block = " ".join(shlex.quote(candidate) for candidate in candidates)
    script_tuple = "(" + ", ".join(repr(name) for name in preferred_scripts) + ("," if len(preferred_scripts) == 1 else "") + ")"
    return (
        "TARGET_PATH=" + target_value + "\n"
        "TEST_TARGET=\"\"\n"
        + (f"for CANDIDATE in {candidate_block}; do\n" if candidate_block else "for CANDIDATE in; do\n")
        + "  if [ -f \"$CANDIDATE\" ]; then TEST_TARGET=\"$CANDIDATE\"; break; fi\n"
        "done\n"
        "if [ -z \"$TEST_TARGET\" ]; then exit 124; fi\n"
        "TARGET_DIR=$(dirname \"$TEST_TARGET\")\n"
        "PKG=\"\"\n"
        "SEARCH_DIR=\"$TARGET_DIR\"\n"
        "while true; do\n"
        "  if [ -f \"$SEARCH_DIR/package.json\" ]; then PKG=\"$SEARCH_DIR/package.json\"; break; fi\n"
        "  if [ \"$SEARCH_DIR\" = \".\" ] || [ \"$SEARCH_DIR\" = \"/\" ]; then break; fi\n"
        "  NEXT_DIR=$(dirname \"$SEARCH_DIR\")\n"
        "  if [ \"$NEXT_DIR\" = \"$SEARCH_DIR\" ]; then break; fi\n"
        "  SEARCH_DIR=\"$NEXT_DIR\"\n"
        "done\n"
        "if [ -z \"$PKG\" ] && [ -f \"package.json\" ]; then PKG=\"package.json\"; fi\n"
        "if [ -z \"$PKG\" ]; then exit 126; fi\n"
        "SCRIPT_INFO=$(TARGET_PKG=\"$PKG\" python3 - <<'PY'\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "pkg = pathlib.Path(os.environ['TARGET_PKG'])\n"
        "data = json.loads(pkg.read_text())\n"
        "scripts = data.get('scripts') or {}\n"
        f"for name in {script_tuple}:\n"
        "    value = scripts.get(name)\n"
        "    if not isinstance(value, str) or not value.strip():\n"
        "        continue\n"
        "    lowered_name = name.strip().lower()\n"
        "    lowered_value = value.strip().lower()\n"
        "    if 'vitest' in lowered_name or 'vitest' in lowered_value:\n"
        "        strategy = 'vitest'\n"
        "    elif 'jest' in lowered_name or 'jest' in lowered_value:\n"
        "        strategy = 'jest'\n"
        "    else:\n"
        "        continue\n"
        "    print(name + '|' + strategy)\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(125)\n"
        "PY\n"
        ")\n"
        "STATUS=$?\n"
        "if [ \"$STATUS\" -eq 125 ]; then exit 125; fi\n"
        "if [ \"$STATUS\" -ne 0 ]; then exit \"$STATUS\"; fi\n"
        "SCRIPT_NAME=${SCRIPT_INFO%%|*}\n"
        "STRATEGY=${SCRIPT_INFO#*|}\n"
        "if [ -z \"$SCRIPT_NAME\" ]; then exit 125; fi\n"
        "if [ -z \"$STRATEGY\" ] || [ \"$STRATEGY\" = \"$SCRIPT_INFO\" ]; then STRATEGY=\"$SCRIPT_NAME\"; fi\n"
        "echo \"__VALIDATION_STRATEGY__:$STRATEGY\"\n"
        "PKG_DIR=$(dirname \"$PKG\")\n"
        "if [ -f \"$PKG_DIR/pnpm-lock.yaml\" ] && command -v pnpm >/dev/null 2>&1; then\n"
        "  (cd \"$PKG_DIR\" && pnpm run \"$SCRIPT_NAME\" -- \"$TEST_TARGET\")\n"
        "elif [ -f \"$PKG_DIR/yarn.lock\" ] && command -v yarn >/dev/null 2>&1; then\n"
        "  (cd \"$PKG_DIR\" && yarn \"$SCRIPT_NAME\" \"$TEST_TARGET\")\n"
        "elif command -v npm >/dev/null 2>&1; then\n"
        "  (cd \"$PKG_DIR\" && npm run \"$SCRIPT_NAME\" -- \"$TEST_TARGET\")\n"
        "else\n"
        "  exit 127\n"
        "fi"
    )


def _typescript_repo_validation_command(target_path: str) -> str:
    return (
        "(\n"
        + _node_project_script_validation_command(target_path, preferred_scripts=("typecheck",))
        + "\n)\n"
        "STATUS=$?\n"
        "if [ \"$STATUS\" -eq 0 ]; then exit 0; fi\n"
        "if [ \"$STATUS\" -ne 125 ] && [ \"$STATUS\" -ne 126 ] && [ \"$STATUS\" -ne 127 ]; then exit \"$STATUS\"; fi\n"
        + _typescript_project_validation_command(target_path)
    )


def _typescript_repo_source_validation_command(target_path: str) -> str:
    return (
        "(\n"
        + _node_project_script_validation_command(
            target_path,
            preferred_scripts=("test:unit", "test:unit:ci", "vitest", "jest", "typecheck", "test", "test:ci"),
        )
        + "\n)\n"
        "STATUS=$?\n"
        "if [ \"$STATUS\" -eq 0 ]; then exit 0; fi\n"
        "if [ \"$STATUS\" -ne 125 ] && [ \"$STATUS\" -ne 126 ] && [ \"$STATUS\" -ne 127 ]; then exit \"$STATUS\"; fi\n"
        + _typescript_project_validation_command(target_path)
    )


def _typescript_source_validation_command(target_path: str) -> str:
    return (
        "(\n"
        + _node_related_test_validation_command(target_path)
        + "\n)\n"
        "STATUS=$?\n"
        "if [ \"$STATUS\" -eq 0 ]; then exit 0; fi\n"
        "if [ \"$STATUS\" -ne 124 ] && [ \"$STATUS\" -ne 125 ] && [ \"$STATUS\" -ne 126 ] && [ \"$STATUS\" -ne 127 ]; then exit \"$STATUS\"; fi\n"
        + _typescript_repo_source_validation_command(target_path)
    )


def _javascript_repo_validation_command(target_path: str) -> str:
    return _node_project_script_validation_command(
        target_path,
        preferred_scripts=("test:unit", "test:unit:ci", "vitest", "jest", "typecheck", "test", "test:ci", "build"),
    )


def _javascript_source_validation_command(target_path: str) -> str:
    return (
        "(\n"
        + _node_related_test_validation_command(target_path)
        + "\n)\n"
        "STATUS=$?\n"
        "if [ \"$STATUS\" -eq 0 ]; then exit 0; fi\n"
        "if [ \"$STATUS\" -ne 124 ] && [ \"$STATUS\" -ne 125 ] && [ \"$STATUS\" -ne 126 ] && [ \"$STATUS\" -ne 127 ]; then exit \"$STATUS\"; fi\n"
        + _javascript_repo_validation_command(target_path)
    )


def _post_write_validation_command(path: str) -> tuple[str, str] | None:
    normalized = path.strip().lower()
    basename = normalized.rsplit("/", 1)[-1]
    if basename == "tsconfig.json":
        return ("TypeScript project", _typescript_repo_validation_command(path))
    if basename in {
        "package.json",
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
        "tailwind.config.js",
        "tailwind.config.cjs",
        "tailwind.config.mjs",
        "tailwind.config.ts",
        "postcss.config.js",
        "postcss.config.cjs",
        "postcss.config.mjs",
        "vite.config.js",
        "vite.config.mjs",
        "vite.config.ts",
        "webpack.config.js",
        "webpack.config.cjs",
        "webpack.config.ts",
    }:
        return ("Node project script", _node_project_script_validation_command(path))
    quoted_path = shlex.quote(path)
    if _is_python_test_path(path):
        return ("Python tests", _python_repo_test_command(path))
    if normalized.endswith(".py"):
        return ("Related Python tests", _related_python_test_validation_command(path))
    if normalized.endswith(".jsx"):
        return ("JavaScript project", _javascript_source_validation_command(path))
    if normalized.endswith((".js", ".mjs", ".cjs")):
        if _is_probable_node_source_path(path):
            return ("JavaScript project", _javascript_source_validation_command(path))
        return ("JavaScript syntax", f"node --check {quoted_path}")
    if normalized.endswith((".sh", ".bash")):
        return ("Bash syntax", f"bash -n {quoted_path}")
    if normalized.endswith((".ts", ".tsx")):
        return ("TypeScript project", _typescript_source_validation_command(path))
    return None


def _format_validation_command_result(validation_name: str, result: dict) -> str:
    strategy = _validation_strategy_from_result(result)
    if result.get("success"):
        return _with_validation_strategy(f"Validation: {validation_name} OK", strategy)
    exit_code = result.get("exit_code")
    error = _strip_validation_strategy_markers(result.get("error") or result.get("output") or "")
    lowered_error = error.lower()
    if exit_code == -1 and result.get("hint"):
        return _format_runtime_failure("Validation unavailable", result)
    if validation_name == "JavaScript syntax":
        if exit_code == 127 or ("node" in lowered_error and "not found" in lowered_error):
            return _with_validation_strategy("Validation skipped: Node.js not available for JavaScript syntax check", strategy)
    if validation_name == "JavaScript project":
        if exit_code == 125:
            return _with_validation_strategy("Validation skipped: no matching node project script found for JavaScript project validation", strategy)
        if exit_code == 126:
            return _with_validation_strategy("Validation skipped: package.json not found for JavaScript project validation", strategy)
        if exit_code == 127 or any(token in lowered_error for token in ("npm", "pnpm", "yarn", "python3", "node")):
            return _with_validation_strategy("Validation skipped: Node project tooling not available for JavaScript project validation", strategy)
    if validation_name == "Node project script":
        if exit_code == 125:
            return _with_validation_strategy("Validation skipped: no matching node project script found (typecheck/build)", strategy)
        if exit_code == 126:
            return _with_validation_strategy("Validation skipped: package.json not found for node project validation", strategy)
        if exit_code == 127 or any(token in lowered_error for token in ("npm", "pnpm", "yarn", "python3")):
            return _with_validation_strategy("Validation skipped: npm/pnpm/yarn tooling not available for node project validation", strategy)
    if validation_name == "Python tests":
        if exit_code == 126:
            return _with_validation_strategy("Validation skipped: no Python test cases found in Python test file", strategy)
        if exit_code == 127 or ("python3" in lowered_error and "not found" in lowered_error) or ("pytest" in lowered_error and ("no module named" in lowered_error or "not found" in lowered_error)):
            return _with_validation_strategy("Validation skipped: Python test tooling not available for Python test validation", strategy)
    if validation_name == "Related Python tests":
        if exit_code == 124:
            return _with_validation_strategy("Validation skipped: no matching Python test file found", strategy)
        if exit_code == 126:
            return _with_validation_strategy("Validation skipped: no Python test cases found in related Python test file", strategy)
        if exit_code == 127 or ("python3" in lowered_error and "not found" in lowered_error) or ("pytest" in lowered_error and ("no module named" in lowered_error or "not found" in lowered_error)):
            return _with_validation_strategy("Validation skipped: Python test tooling not available for related Python test validation", strategy)
    if validation_name == "Bash syntax":
        if exit_code == 127 or ("bash" in lowered_error and "not found" in lowered_error):
            return _with_validation_strategy("Validation skipped: bash not available for shell syntax check", strategy)
    if validation_name == "TypeScript project":
        if exit_code == 126:
            return _with_validation_strategy("Validation skipped: tsconfig.json not found for TypeScript validation", strategy)
        if exit_code == 127 or ("tsc" in lowered_error and "not found" in lowered_error):
            return _with_validation_strategy("Validation skipped: TypeScript compiler not available", strategy)
    summary = error[:300] if error else f"exit {exit_code}"
    failure = f"Validation failed: {validation_name} check failed: {summary}"
    if strategy:
        failure = f"Validation failed: {validation_name} check failed ({strategy}): {summary}"
    return failure


async def _post_write_validation(path: str, content: str) -> str:
    inline_result = _inline_post_write_validation(path, content)
    command_info = _post_write_validation_command(path)
    if inline_result.startswith("Validation failed:"):
        return inline_result
    if inline_result and command_info is None:
        return inline_result
    if inline_result and command_info is not None:
        validation_name, command = command_info
        result = await runtime_manager.execute_bash(command, thread_id=_get_current_thread_id())
        return f"{inline_result}\n{_format_validation_command_result(validation_name, result)}"
    if inline_result:
        return inline_result
    command_info = _post_write_validation_command(path)
    if command_info is None:
        return ""
    validation_name, command = command_info
    result = await runtime_manager.execute_bash(command, thread_id=_get_current_thread_id())
    return _format_validation_command_result(validation_name, result)


def set_thread_context(thread_id: str):
    global _thread_id_fallback
    _thread_ctx.set(thread_id)
    _thread_id_fallback = thread_id


def _get_current_thread_id() -> str:
    val = _thread_ctx.get()
    if val == "_default" and _thread_id_fallback != "_default":
        return _thread_id_fallback
    return val


def set_tool_filter(allowed_tools: list[str] | None = None, disallowed_tools: list[str] | None = None):
    allowed = None if allowed_tools is None else {name.strip() for name in allowed_tools if name and name.strip()}
    disallowed = {name.strip() for name in (disallowed_tools or []) if name and name.strip()}
    return _tool_filter_ctx.set({"allowed": allowed, "disallowed": disallowed})


def clear_tool_filter(token):
    _tool_filter_ctx.reset(token)


def _apply_tool_filter(tools: list) -> list:
    config = _tool_filter_ctx.get()
    if not config:
        return tools
    allowed = config.get("allowed")
    disallowed = config.get("disallowed", set())
    filtered = []
    for tool_obj in tools:
        if tool_obj.name in disallowed:
            continue
        if allowed is not None and tool_obj.name not in allowed:
            continue
        filtered.append(tool_obj)
    return filtered


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
    result = await runtime_manager.execute_python(code, thread_id=_get_current_thread_id())
    if result["success"]:
        output = result["output"]
        if result["error"]:
            output += f"\n[stderr: {result['error'][:500]}]"
        return output if output else "(No output)"
    return _format_runtime_failure("Execution failed", result)


@tool
async def execute_javascript(code: str) -> str:
    """Execute JavaScript code and return the output."""
    result = await runtime_manager.execute_javascript(code, thread_id=_get_current_thread_id())
    if result["success"]:
        output = result["output"]
        if result["error"]:
            output += f"\n[stderr: {result['error'][:500]}]"
        return output if output else "(No output)"
    return _format_runtime_failure("Execution failed", result)


@tool
async def execute_bash(command: str) -> str:
    """Execute a bash shell command. Use for installing packages, running build tools, git operations, etc."""
    result = await runtime_manager.execute_bash(command, thread_id=_get_current_thread_id())
    if result["success"]:
        output = result["output"]
        if result["error"]:
            output += f"\n[stderr: {result['error'][:1000]}]"
        return output if output else "(No output)"
    exit_code = result.get("exit_code", "unknown")
    return _format_runtime_failure(f"Command failed (exit {exit_code})", result)


@tool
async def write_file(path: str, content: str) -> str:
    """Write content to a file in the workspace. Creates directories as needed.
    Use this to create code files, reports, HTML pages, etc."""
    # Track file state before write for streaming diff
    diff_token = None
    try:
        from app.sandbox.manager import sandbox_executor
        thread_id = _get_current_thread_id()
        work_dir = sandbox_executor.get_workspace_dir(thread_id) if thread_id else None
        if work_dir:
            diff_token = sandbox_executor.track_file_before(work_dir, path)
    except Exception:
        pass

    result = await runtime_manager.write_file(path, content, thread_id=_get_current_thread_id())

    # Compute and emit streaming diff
    if result["success"] and diff_token:
        try:
            from app.sandbox.manager import sandbox_executor
            from app.agents.tool_runtime import emit_file_diff
            diff_payload = sandbox_executor.compute_file_diff(diff_token)
            if diff_payload:
                emit_file_diff(diff_payload)
        except Exception:
            pass

    if result["success"]:
        message = f"File written: {path} ({result['size']} bytes)"
        validation = await _post_write_validation(path, content)
        if validation:
            message += f"\n{validation}"
        return message
    return _format_runtime_failure("Write failed", result)


@tool
async def read_file(path: str) -> str:
    """Read the content of a file from the workspace."""
    result = await runtime_manager.read_file(path, thread_id=_get_current_thread_id())
    if result["success"]:
        return result["content"]
    return _format_runtime_failure("Read failed", result)


@tool
async def list_files(path: str = ".") -> str:
    """List files and directories in the workspace."""
    result = await runtime_manager.list_files(path, thread_id=_get_current_thread_id())
    if result["success"]:
        if not result["entries"]:
            return f"Directory '{path}' is empty"
        lines = [f"Contents of {path}/:"]
        for e in result["entries"]:
            prefix = "[DIR]" if e["is_dir"] else "     "
            lines.append(f"  {prefix} {e['name']}")
        return "\n".join(lines)
    return _format_runtime_failure("List failed", result)


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


@tool
def get_editor_state() -> str:
    """Get the current thread-scoped editor state, including active file, open files, cursor, selection, and recent editor diagnostics."""
    thread_id = _get_current_thread_id()
    if not thread_id or thread_id == "_default":
        return "No thread context available for editor state."
    state = editor_state_store.get_state(thread_id)
    if state is None:
        return f"No editor state is available for thread '{thread_id}'."
    return json.dumps(state, ensure_ascii=False, indent=2)


@tool
def get_editor_diagnostics(path: str = "") -> str:
    """Get recent editor diagnostics for the current thread. Optionally filter by a workspace-relative or editor-reported path."""
    thread_id = _get_current_thread_id()
    if not thread_id or thread_id == "_default":
        return "No thread context available for editor diagnostics."
    normalized_path = path.strip() if isinstance(path, str) else ""
    diagnostics = editor_state_store.get_diagnostics(thread_id, path=normalized_path or None)
    if not diagnostics:
        if normalized_path:
            return f"No editor diagnostics are available for '{normalized_path}' in thread '{thread_id}'."
        return f"No editor diagnostics are available for thread '{thread_id}'."
    payload = {
        "thread_id": thread_id,
        "path": normalized_path or None,
        "diagnostics": diagnostics,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _dedupe_tools(tools: list) -> list:
    seen = set()
    result = []
    for tool_obj in tools:
        if tool_obj.name in seen:
            continue
        seen.add(tool_obj.name)
        result.append(tool_obj)
    return result


def _get_dynamic_tools() -> list:
    from app.skills.mcp import mcp_registry

    tools = []
    mcp_tools = mcp_registry.get_langchain_tools()
    if mcp_tools:
        tools.extend(mcp_tools)
    custom = tool_registry.get_custom_tools()
    if custom:
        tools.extend(custom)
    return tools


def _get_raw_tools(include_deferred: bool = True, enable_tool_search: bool | None = None) -> list:
    if enable_tool_search is None:
        enable_tool_search = is_tool_search_enabled()
    tools = list(BASE_TOOLS)
    if enable_tool_search:
        tools.extend(DISCOVERY_TOOLS)
    raw_all = list(BASE_TOOLS) + list(SYSTEM_TOOLS) + list(EVOLUTION_TOOLS) + list(LSP_TOOLS) + _get_dynamic_tools()
    if include_deferred or not enable_tool_search:
        tools.extend(raw_all)
    else:
        tools.extend([tool_obj for tool_obj in raw_all if not should_defer_tool(tool_obj.name)])
    return _apply_tool_filter(_dedupe_tools(tools))


def get_tool_by_name(tool_name: str, include_deferred: bool = True, wrap: bool = True):
    tools = _get_raw_tools(include_deferred=include_deferred)
    for tool_obj in tools:
        if tool_obj.name == tool_name:
            return wrap_langchain_tool(tool_obj) if wrap else tool_obj
    return None


@tool
def tool_search(query: str) -> str:
    """Search available tools and discover deferred capabilities when you need a tool that is not currently exposed."""
    tools = _get_raw_tools(include_deferred=True, enable_tool_search=True)
    results = search_tool_catalog(tools, query, limit=8)
    if not results:
        return f"No tools matched '{query}'."
    lines = ["Matching tools:"]
    for result in results:
        suffix = " [deferred]" if result["should_defer"] else ""
        hints = ", ".join(result["search_hints"][:4])
        lines.append(f"- {result['name']}{suffix}: {result['description']}")
        if hints:
            lines.append(f"  hints: {hints}")
        if result["should_defer"]:
            lines.append(f"  use with run_discovered_tool and a JSON argument object")
    return "\n".join(lines)


@tool
async def run_discovered_tool(tool_name: str, arguments_json: str = "{}") -> str:
    """Run a deferred or discovered tool by name after finding it with tool_search. Arguments must be a JSON object string."""
    if tool_name in {"tool_search", "run_discovered_tool"}:
        return f"Cannot dispatch internal discovery tool '{tool_name}'."
    if not can_use_discovered_tool(tool_name):
        return f"Tool '{tool_name}' has not been discovered yet. Call tool_search first."
    tool_obj = get_tool_by_name(tool_name, include_deferred=True, wrap=True)
    if tool_obj is None:
        return f"Tool '{tool_name}' was not found."
    try:
        parsed = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as exc:
        return f"Invalid arguments_json: {exc}"
    if not isinstance(parsed, dict):
        return "arguments_json must decode to a JSON object."
    return await tool_obj.ainvoke(parsed)


@tool
async def execute_code(code: str) -> str:
    """Execute a Hermes-style programmatic tool script in a single tool call."""
    return await execute_tool_chain.ainvoke({"code": code})


@tool
async def execute_tool_chain(code: str) -> str:
    """Execute a Python script that can call multiple tools programmatically in sequence.

    This is a **Programmatic Tool Calling** feature (Hermes pattern) — instead of
    requiring one LLM inference per tool call, you write a Python script that chains
    multiple tool calls in a single turn, dramatically reducing round-trips.

    Available in the script namespace:
      - await tools.web_search(query="...")
      - await tools.web_fetch(url="...")
      - await tools.execute_python(code="...")
      - await tools.execute_bash(command="...")
      - await tools.write_file(path="...", content="...")
      - await tools.read_file(path="...")
      - await tools.list_files(directory="...")
      - await tools.http_request(url="...", method="GET")
      - await tools.run(tool_name, **kwargs)  # call any tool by name
      - results  # list — append to this to collect outputs
      - print()  # captured in output

    Example:
      ```
      # Search, fetch, and save in one turn
      hits = await tools.web_search(query="Python 3.13 release date")
      page = await tools.web_fetch(url="https://docs.python.org/3/whatsnew/3.13.html")
      await tools.write_file(path="python313.md", content=page[:2000])
      results.append(f"Saved {len(page)} chars")
      ```
    """
    import asyncio as _asyncio
    import io as _io
    import contextlib as _contextlib

    class _ToolProxy:
        """Provides a clean namespace for calling tools by name."""
        def __getattr__(self, name: str):
            async def _invoke(**kwargs):
                tool_obj = get_tool_by_name(name, include_deferred=True, wrap=True)
                if tool_obj is None:
                    return f"Tool '{name}' not found."
                return await tool_obj.ainvoke(kwargs)
            return _invoke

        async def run(self, tool_name: str, **kwargs):
            tool_obj = get_tool_by_name(tool_name, include_deferred=True, wrap=True)
            if tool_obj is None:
                return f"Tool '{tool_name}' not found."
            return await tool_obj.ainvoke(kwargs)

    results: list = []
    proxy = _ToolProxy()
    stdout_buf = _io.StringIO()
    local_ns = {"tools": proxy, "results": results, "json": json}

    try:
        compiled = compile(code, "<tool_chain>", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except SyntaxError as e:
        return f"SyntaxError: {e}"

    try:
        with _contextlib.redirect_stdout(stdout_buf):
            coro = eval(compiled, {"__builtins__": __builtins__}, local_ns)
            if coro is not None:
                await coro
    except Exception as e:
        return f"Error during tool chain execution: {type(e).__name__}: {e}\n\nPartial output:\n{stdout_buf.getvalue()}"

    parts = []
    captured = stdout_buf.getvalue()
    if captured.strip():
        parts.append(captured.strip())
    if results:
        parts.append("\n".join(str(r) for r in results))
    return "\n".join(parts) if parts else "(tool chain completed with no output)"


BASE_TOOLS = [
    web_search, web_fetch, execute_python, execute_javascript, execute_bash,
    write_file, read_file, list_files, get_current_time, calculate,
    get_editor_state, get_editor_diagnostics,
    execute_code,
    execute_tool_chain,
] 


DISCOVERY_TOOLS = [tool_search, run_discovered_tool]


ALL_TOOLS = BASE_TOOLS + SYSTEM_TOOLS + EVOLUTION_TOOLS + DISCOVERY_TOOLS + LSP_TOOLS


def get_all_tools(include_deferred: bool = True, enable_tool_search: bool | None = None, wrap: bool = True) -> list:
    """Return built-in, dynamic, and optionally deferred tools."""
    tools = _get_raw_tools(include_deferred=include_deferred, enable_tool_search=enable_tool_search)
    if not wrap:
        return tools
    return [wrap_langchain_tool(tool_obj) for tool_obj in tools]
