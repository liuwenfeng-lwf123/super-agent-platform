from __future__ import annotations

import contextvars
import fnmatch
import json
import logging
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from langchain_core.tools import StructuredTool

from app.config import settings

logger = logging.getLogger(__name__)

PERMISSION_RULES_PATH = Path(settings.data_dir) / "tool_permission_rules.json"
POLICY_LIMITS_PATH = Path(settings.data_dir) / "policy_limits.json"
TOOL_EVENTS_PATH = Path(settings.data_dir) / "tool_events.jsonl"
PERMISSION_REQUEST_TIMEOUT_SECONDS = 60


@dataclass
class ToolMetadata:
    name: str
    category: str = "general"
    search_hints: list[str] = field(default_factory=list)
    is_read_only: bool = False
    is_destructive: bool = False
    is_concurrency_safe: bool = False
    should_defer: bool = False
    default_permission: str = "allow"
    track_usage: bool = True


@dataclass
class PermissionResult:
    decision: str
    reason: str = ""
    matched_rule: str | None = None
    source: str = "default"


@dataclass
class RuntimeContext:
    thread_id: str = ""
    agent_id: str = ""
    mode: str = "standard"
    discovered_tools: set[str] = field(default_factory=set)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    permission_events: list[dict[str, Any]] = field(default_factory=list)
    hook_events: list[dict[str, Any]] = field(default_factory=list)
    tool_summary_cursor: int = 0
    permission_cursor: int = 0
    hook_cursor: int = 0


CATEGORY_MAP = {
    "web_search": "search",
    "web_fetch": "search",
    "summarize_url": "search",
    "http_request": "network",
    "read_file": "file",
    "list_files": "file",
    "write_file": "file",
    "pdf_extract": "file",
    "execute_python": "execution",
    "execute_javascript": "execution",
    "execute_bash": "execution",
    "get_current_time": "utility",
    "calculate": "utility",
    "screenshot": "system",
    "clipboard_read": "system",
    "clipboard_write": "system",
    "system_info": "system",
    "open_app": "system",
    "open_url": "system",
    "browser_open": "system",
    "browser_get_state": "system",
    "browser_run_javascript": "system",
    "browser_click": "system",
    "browser_fill": "system",
    "browser_extract_text": "system",
    "notify": "system",
    "git_command": "execution",
    "create_tool": "evolution",
    "list_custom_tools": "evolution",
    "remove_custom_tool": "evolution",
    "create_skill": "evolution",
    "patch_skill": "evolution",
    "edit_skill": "evolution",
    "rollback_skill": "evolution",
    "list_custom_skills": "evolution",
    "view_skill": "evolution",
    "view_evolution_log": "evolution",
    "score_skill": "evolution",
    "write_skill_file": "evolution",
    "remove_skill_file": "evolution",
    "record_skill_feedback": "evolution",
    "gepa_evolve": "evolution",
    "semantic_check": "evolution",
    "execute_code": "evolution",
    "spawn_agent": "agents",
    "send_agent_message": "agents",
    "register_hook": "hooks",
    "fire_hook": "hooks",
    "manage_plugin": "plugins",
    "manage_cron": "cron",
    "elicit_input": "elicitation",
    "remember": "memory",
    "file_history": "audit",
    "knowledge_search": "rag",
    "session_search": "memory",
    "tool_search": "discovery",
    "run_discovered_tool": "discovery",
    "local_execute_bash": "local",
    "local_read_file": "local",
    "local_write_file": "local",
    "local_list_files": "local",
    "local_execute_python": "local",
    "local_open_app": "local",
    "local_get_system_info": "local",
    "local_upload_to_workspace": "local",
    "local_download_from_workspace": "local",
}

SEARCH_HINTS_MAP = {
    "web_search": ["internet", "search", "web", "research", "current"],
    "web_fetch": ["read url", "fetch article", "documentation", "web page"],
    "summarize_url": ["article", "docs", "read webpage", "summary"],
    "http_request": ["api", "http", "rest", "fetch endpoint"],
    "read_file": ["read file", "open file", "inspect code"],
    "list_files": ["directory", "tree", "list workspace"],
    "write_file": ["create file", "edit file", "save file"],
    "execute_python": ["python", "script", "analysis", "run code"],
    "execute_javascript": ["javascript", "node", "run js"],
    "execute_bash": ["shell", "terminal", "command line", "bash"],
    "git_command": ["git", "diff", "status", "history"],
    "pdf_extract": ["pdf", "extract text", "document"],
    "tool_search": ["find tool", "search tool", "discover capability"],
    "run_discovered_tool": ["invoke tool", "call discovered tool"],
    "local_execute_bash": ["local shell", "computer terminal", "run locally"],
    "local_read_file": ["local file", "read on computer"],
    "local_write_file": ["local write", "save on computer"],
    "local_list_files": ["browse computer", "local directory"],
    "local_execute_python": ["local python", "run on computer"],
    "local_open_app": ["launch app", "open application"],
    "local_get_system_info": ["computer info", "host info", "machine info"],
    "local_upload_to_workspace": ["upload file", "copy into workspace"],
    "local_download_from_workspace": ["download file", "copy to computer"],
    "screenshot": ["screen", "capture", "image"],
    "clipboard_read": ["clipboard", "pasteboard", "copy text"],
    "clipboard_write": ["clipboard", "pasteboard", "copy text"],
    "system_info": ["os", "cpu", "memory", "disk"],
    "open_app": ["launch program", "desktop app"],
    "open_url": ["browser", "url", "website"],
    "browser_open": ["browser", "open website", "navigate page", "url"],
    "browser_get_state": ["browser state", "current tab", "page info", "url", "title"],
    "browser_run_javascript": ["browser automation", "dom", "run js", "page script"],
    "browser_click": ["click page", "button", "browser automation", "selector"],
    "browser_fill": ["type in browser", "fill form", "input field", "selector"],
    "browser_extract_text": ["extract page text", "read webpage", "selector", "browser content"],
    "notify": ["notification", "desktop alert"],
    "create_tool": ["new tool", "extend capability"],
    "list_custom_tools": ["custom tool", "tool registry"],
    "remove_custom_tool": ["delete tool", "remove custom tool"],
    "create_skill": ["new skill", "persona"],
    "list_custom_skills": ["skill registry", "custom skill"],
    "view_evolution_log": ["history", "evolution", "audit"],
    "remember": ["memory", "remember", "save", "recall", "preference"],
    "file_history": ["file changes", "diff", "history", "audit", "what changed"],
    "knowledge_search": ["knowledge", "rag", "documents", "search docs", "find in docs"],
    "session_search": ["past conversations", "previous session", "recall", "history", "what did we discuss"],
}

READ_ONLY_TOOL_NAMES = {
    "web_search",
    "web_fetch",
    "read_file",
    "list_files",
    "get_current_time",
    "calculate",
    "system_info",
    "browser_get_state",
    "browser_extract_text",
    "summarize_url",
    "pdf_extract",
    "local_read_file",
    "local_list_files",
    "local_get_system_info",
    "tool_search",
    "knowledge_search",
    "session_search",
}

DESTRUCTIVE_TOOL_NAMES = {
    "execute_bash",
    "write_file",
    "git_command",
    "clipboard_write",
    "open_app",
    "open_url",
    "notify",
    "create_tool",
    "remove_custom_tool",
    "create_skill",
    "local_execute_bash",
    "local_write_file",
    "local_execute_python",
    "local_open_app",
    "local_upload_to_workspace",
    "local_download_from_workspace",
}

DEFAULT_ASK_TOOL_NAMES = {
    "screenshot",
    "clipboard_read",
    "clipboard_write",
    "open_app",
    "open_url",
    "notify",
    "local_execute_bash",
    "local_read_file",
    "local_write_file",
    "local_list_files",
    "local_execute_python",
    "local_open_app",
    "local_get_system_info",
    "local_upload_to_workspace",
    "local_download_from_workspace",
}

DEFERRED_TOOL_NAMES = {
    "screenshot",
    "clipboard_read",
    "clipboard_write",
    "open_app",
    "open_url",
    "notify",
    "git_command",
    "pdf_extract",
    "create_tool",
    "list_custom_tools",
    "remove_custom_tool",
    "create_skill",
    "list_custom_skills",
    "view_evolution_log",
    "file_history",
    "local_execute_bash",
    "local_read_file",
    "local_write_file",
    "local_list_files",
    "local_execute_python",
    "local_open_app",
    "local_get_system_info",
    "local_upload_to_workspace",
    "local_download_from_workspace",
}

INTERNAL_DISCOVERY_TOOLS = {"tool_search", "run_discovered_tool"}

# Dynamic description overrides — can be set at runtime for context-aware tool descriptions
_DYNAMIC_DESCRIPTIONS: dict[str, str] = {}

CONCURRENCY_SAFE_TOOLS = {
    "web_search",
    "web_fetch",
    "summarize_url",
    "read_file",
    "list_files",
    "get_current_time",
    "calculate",
    "system_info",
    "pdf_extract",
    "tool_search",
    "http_request",
    "knowledge_search",
    "session_search",
    "local_read_file",
    "local_list_files",
    "local_get_system_info",
}


class PermissionRuleStore:
    def __init__(self):
        self._rules = {
            "always_allow": [],
            "always_deny": [],
            "always_ask": [],
        }
        self._load()

    def _load(self):
        try:
            if PERMISSION_RULES_PATH.exists():
                data = json.loads(PERMISSION_RULES_PATH.read_text(encoding="utf-8"))
                self.set_rules(data, persist=False)
        except Exception as exc:
            logger.warning("Failed to load tool permission rules: %s", exc)
        # Layer in scoped rules (Claude Code: Managed/User/Project/Local)
        try:
            from app.agents.permission_scopes import get_effective_rules
            layered = get_effective_rules()
            if any(layered.values()):
                # Merge with current rules: scoped additions + explicit single-file rules,
                # but preserve order so deny/ask entries take effect.
                merged = {k: list(self._rules.get(k, [])) for k in ("always_allow", "always_deny", "always_ask")}
                for bucket in merged:
                    for pat in layered.get(bucket, []):
                        if pat not in merged[bucket]:
                            merged[bucket].append(pat)
                self._rules = merged
        except Exception as exc:
            logger.warning("Failed to load scoped permission rules: %s", exc)

    def reload_scoped_rules(self):
        """Re-read scoped permission files (Managed/User/Project/Local) without disk write."""
        try:
            from app.agents.permission_scopes import load_layered_rules
            merged, _detail = load_layered_rules()
            # Preserve any single-file rules at PERMISSION_RULES_PATH
            if PERMISSION_RULES_PATH.exists():
                try:
                    base = json.loads(PERMISSION_RULES_PATH.read_text(encoding="utf-8"))
                    for bucket in merged:
                        existing = base.get(bucket, []) if isinstance(base, dict) else []
                        for pat in existing:
                            if pat not in merged[bucket]:
                                merged[bucket].insert(0, pat)
                except Exception as e:
                    logger.debug("Suppressed error in tool_runtime: %s", e)
            self._rules = merged
        except Exception as exc:
            logger.warning("Failed to reload scoped permission rules: %s", exc)

    def _save(self):
        PERMISSION_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        PERMISSION_RULES_PATH.write_text(
            json.dumps(self._rules, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_rules(self) -> dict[str, list[str]]:
        return {
            key: list(value)
            for key, value in self._rules.items()
        }

    def set_rules(self, data: dict[str, Any], persist: bool = True):
        normalized = {
            "always_allow": [],
            "always_deny": [],
            "always_ask": [],
        }
        for key in normalized:
            values = data.get(key, []) if isinstance(data, dict) else []
            normalized[key] = [str(v).strip() for v in values if str(v).strip()]
        self._rules = normalized
        if persist:
            self._save()

    def match(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None]:
        """Match a tool call against the permission rules.

        Supports two pattern styles (first matching rule wins, deny > ask > allow):
          1. Bare glob:        "Bash"  or  "read_*"         (matches on tool name only)
          2. Tool(specifier):  "Bash(git diff *)"           (matches on tool name AND
                               the primary arg of the tool input, using fnmatch)

        For `Tool(specifier)`, the "primary arg" is the first of these that exists in
        tool_input: command / cmd / query / path / file / url / content.
        """
        for rule_type, decision in (
            ("always_deny", "deny"),
            ("always_ask", "ask"),
            ("always_allow", "allow"),
        ):
            for pattern in self._rules.get(rule_type, []):
                if _pattern_matches(pattern, tool_name, tool_input or {}):
                    return decision, pattern
        return None, None


class PolicyLimitStore:
    def __init__(self):
        self._restrictions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self):
        try:
            if POLICY_LIMITS_PATH.exists():
                data = json.loads(POLICY_LIMITS_PATH.read_text(encoding="utf-8"))
                self.set_restrictions(data, persist=False)
        except Exception as exc:
            logger.warning("Failed to load policy limits: %s", exc)

    def _save(self):
        POLICY_LIMITS_PATH.parent.mkdir(parents=True, exist_ok=True)
        POLICY_LIMITS_PATH.write_text(
            json.dumps({"restrictions": self._restrictions}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_restrictions(self) -> dict[str, dict[str, Any]]:
        return {
            key: dict(value)
            for key, value in self._restrictions.items()
        }

    def set_restrictions(self, data: dict[str, Any], persist: bool = True):
        restrictions = data.get("restrictions", data) if isinstance(data, dict) else {}
        normalized: dict[str, dict[str, Any]] = {}
        if isinstance(restrictions, dict):
            for key, value in restrictions.items():
                if isinstance(value, dict):
                    allowed = bool(value.get("allowed", True))
                    reason = str(value.get("reason", "")).strip()
                else:
                    allowed = bool(value)
                    reason = ""
                normalized[str(key)] = {
                    "allowed": allowed,
                    "reason": reason,
                }
        self._restrictions = normalized
        if persist:
            self._save()

    def get_entry(self, key: str) -> dict[str, Any] | None:
        return self._restrictions.get(key)

    def is_allowed(self, key: str, default: bool = True) -> bool:
        entry = self.get_entry(key)
        if entry is None:
            return default
        return bool(entry.get("allowed", True))


class PermissionRequestManager:
    def __init__(self):
        self._pending: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def _public_request(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "request_id": request["request_id"],
            "thread_id": request["thread_id"],
            "agent_id": request["agent_id"],
            "mode": request["mode"],
            "tool": request["tool"],
            "input": request["input"],
            "reason": request["reason"],
            "source": request["source"],
            "status": request["status"],
            "created_at": request["created_at"],
            "resolved_at": request.get("resolved_at"),
            "resolution_note": request.get("resolution_note", ""),
        }

    def subscribe(self, thread_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(thread_id, []).append(queue)
        return queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue):
        queues = self._subscribers.get(thread_id, [])
        if queue in queues:
            queues.remove(queue)
        if not queues and thread_id in self._subscribers:
            del self._subscribers[thread_id]

    def has_subscribers(self, thread_id: str) -> bool:
        return bool(self._subscribers.get(thread_id))

    async def _publish(self, thread_id: str, payload: dict[str, Any]):
        for queue in list(self._subscribers.get(thread_id, [])):
            try:
                queue.put_nowait(payload)
            except Exception as e:
                logger.debug("Suppressed error in tool_runtime: %s", e)
                continue

    def get_pending(self, thread_id: str | None = None) -> list[dict[str, Any]]:
        pending = []
        for request in self._pending.values():
            if thread_id and request["thread_id"] != thread_id:
                continue
            pending.append(self._public_request(request))
        pending.sort(key=lambda item: item["created_at"])
        return pending

    async def request_permission(
        self,
        *,
        thread_id: str,
        agent_id: str,
        mode: str,
        tool_name: str,
        tool_input: dict[str, Any],
        reason: str,
        source: str,
    ) -> PermissionResult:
        if not thread_id or not self.has_subscribers(thread_id):
            return PermissionResult(
                decision="deny",
                reason=f"No interactive permission channel for {tool_name}",
                source="system",
            )
        request_id = str(uuid.uuid4())[:8]
        request = {
            "request_id": request_id,
            "thread_id": thread_id,
            "agent_id": agent_id,
            "mode": mode,
            "tool": tool_name,
            "input": _preview(tool_input, 240),
            "reason": reason,
            "source": source,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "resolution_note": "",
            "future": asyncio.get_running_loop().create_future(),
        }
        self._pending[request_id] = request
        await self._publish(
            thread_id,
            {"type": "permission_request", "data": self._public_request(request)},
        )
        try:
            approved, note = await asyncio.wait_for(request["future"], timeout=PERMISSION_REQUEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            request["status"] = "denied"
            request["resolved_at"] = datetime.now().isoformat()
            request["resolution_note"] = f"Permission request timed out after {PERMISSION_REQUEST_TIMEOUT_SECONDS}s"
            await self._publish(
                thread_id,
                {"type": "permission_request", "data": self._public_request(request)},
            )
            return PermissionResult(
                decision="deny",
                reason=request["resolution_note"],
                source="system",
            )
        finally:
            self._pending.pop(request_id, None)
        return PermissionResult(
            decision="allow" if approved else "deny",
            reason=note or ("Approved by user" if approved else "Denied by user"),
            source="user",
        )

    async def resolve_request(self, request_id: str, approve: bool, note: str = "") -> dict[str, Any] | None:
        request = self._pending.get(request_id)
        if request is None:
            return None
        if request["future"].done():
            return self._public_request(request)
        request["status"] = "approved" if approve else "denied"
        request["resolved_at"] = datetime.now().isoformat()
        request["resolution_note"] = note.strip()
        await self._publish(
            request["thread_id"],
            {"type": "permission_request", "data": self._public_request(request)},
        )
        request["future"].set_result((approve, request["resolution_note"]))
        return self._public_request(request)


permission_rules = PermissionRuleStore()
policy_limits = PolicyLimitStore()
permission_requests = PermissionRequestManager()
_TOOL_EVENT_LOG: list[dict[str, Any]] = []
_FILE_DIFF_BUFFER: list[dict[str, Any]] = []

_RUNTIME_CONTEXT: contextvars.ContextVar[RuntimeContext | None] = contextvars.ContextVar(
    "tool_runtime_context",
    default=None,
)

PreToolHook = Callable[[str, dict[str, Any], ToolMetadata], PermissionResult | None]
_PRE_TOOL_HOOKS: list[PreToolHook] = []


def set_runtime_context(thread_id: str = "", agent_id: str = "", mode: str = "standard"):
    ctx = RuntimeContext(thread_id=thread_id, agent_id=agent_id, mode=mode)
    return _RUNTIME_CONTEXT.set(ctx)


def clear_runtime_context(token=None):
    if token is not None:
        _RUNTIME_CONTEXT.reset(token)
    else:
        _RUNTIME_CONTEXT.set(None)


def get_runtime_context() -> RuntimeContext:
    ctx = _RUNTIME_CONTEXT.get()
    if ctx is None:
        ctx = RuntimeContext()
        _RUNTIME_CONTEXT.set(ctx)
    return ctx


def register_pre_tool_hook(hook: PreToolHook):
    if hook not in _PRE_TOOL_HOOKS:
        _PRE_TOOL_HOOKS.append(hook)


def _policy_candidates(key: str) -> list[str]:
    ctx = get_runtime_context()
    keys: list[str] = []
    if ctx.thread_id:
        keys.append(f"{key}.thread.{ctx.thread_id}")
    if ctx.mode:
        keys.append(f"{key}.mode.{ctx.mode}")
    if ctx.agent_id:
        keys.append(f"{key}.agent.{ctx.agent_id}")
    keys.append(key)
    return keys


def get_policy_entry(key: str) -> tuple[str, dict[str, Any]] | None:
    for candidate in _policy_candidates(key):
        entry = policy_limits.get_entry(candidate)
        if entry is not None:
            return candidate, entry
    return None


def is_policy_allowed(key: str, default: bool = True) -> bool:
    matched = get_policy_entry(key)
    if matched is None:
        return default
    _, entry = matched
    return bool(entry.get("allowed", True))


def is_tool_search_enabled() -> bool:
    return bool(settings.enable_tool_search) and is_policy_allowed("feature.tool_search", True)


def _default_permission_for(name: str) -> str:
    if name in DEFAULT_ASK_TOOL_NAMES:
        return "ask"
    return "allow"


def get_tool_metadata(name: str) -> ToolMetadata:
    category = CATEGORY_MAP.get(name, "general")
    if name.startswith("mcp_"):
        category = "mcp"
    elif name.startswith("local_"):
        category = "local"
    elif name not in CATEGORY_MAP and name not in INTERNAL_DISCOVERY_TOOLS:
        category = "custom"
    should_defer = name in DEFERRED_TOOL_NAMES or name.startswith("mcp_") or category == "custom"
    is_read_only = name in READ_ONLY_TOOL_NAMES
    is_destructive = name in DESTRUCTIVE_TOOL_NAMES
    is_concurrency_safe = name in CONCURRENCY_SAFE_TOOLS
    default_permission = _default_permission_for(name)
    track_usage = name not in INTERNAL_DISCOVERY_TOOLS
    return ToolMetadata(
        name=name,
        category=category,
        search_hints=list(SEARCH_HINTS_MAP.get(name, [])),
        is_read_only=is_read_only,
        is_destructive=is_destructive,
        is_concurrency_safe=is_concurrency_safe,
        should_defer=should_defer,
        default_permission=default_permission,
        track_usage=track_usage,
    )


def should_defer_tool(name: str) -> bool:
    if not is_tool_search_enabled():
        return False
    if not is_policy_allowed("feature.deferred_tools", True):
        return False
    return get_tool_metadata(name).should_defer


def set_dynamic_description(tool_name: str, description: str):
    """Override a tool's description at runtime for context-aware behavior."""
    _DYNAMIC_DESCRIPTIONS[tool_name] = description


def clear_dynamic_description(tool_name: str):
    _DYNAMIC_DESCRIPTIONS.pop(tool_name, None)


def get_effective_description(tool) -> str:
    """Get the dynamic description if set, otherwise the static one."""
    return _DYNAMIC_DESCRIPTIONS.get(tool.name, tool.description)


def describe_tool(tool) -> dict[str, Any]:
    metadata = get_tool_metadata(tool.name)
    return {
        "name": tool.name,
        "description": get_effective_description(tool),
        "category": metadata.category,
        "is_read_only": metadata.is_read_only,
        "is_destructive": metadata.is_destructive,
        "is_concurrency_safe": metadata.is_concurrency_safe,
        "should_defer": metadata.should_defer,
        "search_hints": metadata.search_hints,
    }


def search_tool_catalog(tools: list, query: str, limit: int = 8) -> list[dict[str, Any]]:
    query_tokens = [token for token in query.lower().split() if token]
    ranked: list[tuple[int, dict[str, Any]]] = []
    for tool in tools:
        if tool.name in INTERNAL_DISCOVERY_TOOLS:
            continue
        descriptor = describe_tool(tool)
        haystack = " ".join(
            [
                descriptor["name"],
                descriptor["description"],
                " ".join(descriptor["search_hints"]),
                descriptor["category"],
            ]
        ).lower()
        score = 0
        for token in query_tokens:
            if token in descriptor["name"].lower():
                score += 4
            elif token in haystack:
                score += 1
        if not query_tokens:
            score = 1
            if descriptor["should_defer"]:
                score += 1
        if score <= 0:
            continue
        ranked.append((score, descriptor))
    ranked.sort(key=lambda item: (-item[0], item[1]["name"]))
    results = [descriptor for _, descriptor in ranked[:limit]]
    ctx = get_runtime_context()
    for descriptor in results:
        ctx.discovered_tools.add(descriptor["name"])
    return results


def can_use_discovered_tool(tool_name: str) -> bool:
    metadata = get_tool_metadata(tool_name)
    if not metadata.should_defer:
        return True
    ctx = get_runtime_context()
    return tool_name in ctx.discovered_tools


def _policy_limit_result(tool_name: str, metadata: ToolMetadata) -> PermissionResult | None:
    entries = [
        f"tool.{tool_name}",
        f"category.{metadata.category}",
    ]
    for key in entries:
        matched = get_policy_entry(key)
        if matched is None:
            continue
        matched_key, entry = matched
        if not bool(entry.get("allowed", True)):
            reason = str(entry.get("reason", "")).strip() or f"Blocked by policy limit: {matched_key}"
            return PermissionResult(decision="deny", reason=reason, source="policy")
    return None


def _security_policy_result(tool_name: str, tool_input: dict[str, Any], metadata: ToolMetadata) -> PermissionResult | None:
    from app.security.policy import evaluate_tool_security

    result = evaluate_tool_security(
        tool_name,
        tool_input,
        category=metadata.category,
        is_read_only=metadata.is_read_only,
        is_destructive=metadata.is_destructive,
        mode=get_runtime_context().mode,
    )
    if result is None:
        return None
    return PermissionResult(
        decision=result.decision.value,
        reason=result.reason,
        source=result.source,
    )


def _bash_safety_result(tool_name: str, tool_input: dict[str, Any]) -> PermissionResult | None:
    command = ""
    if tool_name in {"execute_bash", "local_execute_bash"}:
        command = str(tool_input.get("command", "")).strip()
    elif tool_name == "git_command":
        command = f"git {str(tool_input.get('command', '')).strip()}".strip()
    if not command:
        return None
    from app.agents.orchestrator import check_bash_safety
    safety = check_bash_safety(command)
    warnings = "; ".join(safety.get("warnings", [])[:3])
    if not safety.get("safe", True):
        reason = warnings or "Unsafe shell command"
        return PermissionResult(decision="deny", reason=reason, source="hook")
    if warnings and not safety.get("is_read_only", False):
        return PermissionResult(decision="ask", reason=warnings, source="hook")
    return None


def _speculation_result(tool_name: str, metadata: ToolMetadata) -> PermissionResult | None:
    ctx = get_runtime_context()
    if ctx.mode != "speculation":
        return None
    if metadata.is_read_only:
        return None
    if tool_name == "write_file":
        try:
            from app.runtime_backends import runtime_manager

            if ctx.thread_id and runtime_manager.is_shadow_thread(ctx.thread_id):
                return None
        except Exception as e:
            logger.debug("Suppressed error in tool_runtime: %s", e)
    return PermissionResult(
        decision="deny",
        reason=f"Speculation mode only allows read-only tools and shadow-safe file writes: {tool_name}",
        source="hook",
    )


def _default_hook(tool_name: str, tool_input: dict[str, Any], metadata: ToolMetadata) -> PermissionResult | None:
    security_result = _security_policy_result(tool_name, tool_input, metadata)
    if security_result:
        return security_result
    speculation_result = _speculation_result(tool_name, metadata)
    if speculation_result:
        return speculation_result
    policy_result = _policy_limit_result(tool_name, metadata)
    if policy_result:
        return policy_result
    bash_result = _bash_safety_result(tool_name, tool_input)
    if bash_result:
        return bash_result
    if get_runtime_context().mode == "local" and tool_name == "browser_open":
        return PermissionResult(
            decision="ask",
            reason=f"Explicit approval required for {tool_name}",
            source="default",
        )
    if metadata.default_permission == "ask":
        return PermissionResult(
            decision="ask",
            reason=f"Explicit approval required for {tool_name}",
            source="default",
        )
    return None


register_pre_tool_hook(_default_hook)


# Arg keys searched (in order) for Tool(specifier) matching.
_PRIMARY_ARG_KEYS = ("command", "cmd", "query", "path", "file", "url", "content")


def _extract_primary_arg(tool_input: dict[str, Any]) -> str:
    for key in _PRIMARY_ARG_KEYS:
        if key in tool_input and tool_input[key] is not None:
            val = tool_input[key]
            return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
    # Fallback: stringify whole input
    try:
        return json.dumps(tool_input, ensure_ascii=False)
    except Exception as e:
        logger.debug("Suppressed error in tool_runtime: %s", e)
        return str(tool_input)


def _parse_tool_rule(pattern: str) -> tuple[str, str | None]:
    """Parse 'Bash(git diff *)' -> ('Bash', 'git diff *').

    Plain patterns without parens return (pattern, None).
    """
    p = pattern.strip()
    if "(" in p and p.endswith(")"):
        head, _, rest = p.partition("(")
        spec = rest[:-1]  # drop trailing ')'
        return head.strip(), spec.strip()
    return p, None


def _pattern_matches(pattern: str, tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Check if a rule pattern matches this tool invocation."""
    name_pat, arg_pat = _parse_tool_rule(pattern)
    if not fnmatch.fnmatch(tool_name, name_pat):
        return False
    if arg_pat is None:
        return True
    primary = _extract_primary_arg(tool_input)
    return fnmatch.fnmatch(primary, arg_pat)


def _should_drop_auto_mode_allow_rule(pattern: str, tool_name: str) -> bool:
    ctx = get_runtime_context()
    if ctx.mode != "auto":
        return False
    name_pat, arg_pat = _parse_tool_rule(pattern)
    normalized = name_pat.strip().lower()
    broad_targets = {
        "execute_bash",
        "local_execute_bash",
        "git_command",
        "execute_python",
        "local_execute_python",
        "execute_javascript",
        "spawn_agent",
        "send_agent_message",
        "bash",
        "python",
        "javascript",
        "agent",
    }
    if normalized not in broad_targets and tool_name not in {
        "execute_bash",
        "local_execute_bash",
        "git_command",
        "execute_python",
        "local_execute_python",
        "execute_javascript",
        "spawn_agent",
        "send_agent_message",
    }:
        return False
    if arg_pat is None:
        return True
    return any(ch in arg_pat for ch in "*?[]")


def _build_rule_result(tool_name: str, tool_input: dict[str, Any] | None = None) -> PermissionResult | None:
    decision, pattern = permission_rules.match(tool_name, tool_input)
    if not decision:
        return None
    if decision == "allow" and pattern and _should_drop_auto_mode_allow_rule(pattern, tool_name):
        return None
    return PermissionResult(
        decision=decision,
        reason=f"Matched permission rule: {pattern}",
        matched_rule=pattern,
        source="rule",
    )


def _preview(value: Any, limit: int = 240) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _append_permission_event(tool_name: str, tool_input: dict[str, Any], result: PermissionResult):
    ctx = get_runtime_context()
    metadata = get_tool_metadata(tool_name)
    input_preview = _preview(tool_input, 200)
    ctx.permission_events.append(
        {
            "tool": tool_name,
            "input": input_preview,
            "decision": result.decision,
            "reason": result.reason,
            "source": result.source,
            "matched_rule": result.matched_rule,
        }
    )
    if len(ctx.permission_events) > 100:
        ctx.permission_events = ctx.permission_events[-50:]
        ctx.permission_cursor = min(ctx.permission_cursor, len(ctx.permission_events))
    try:
        from app.security.audit import build_audit_event, security_audit_log
        from app.security.policy import classify_tool

        risk_level = classify_tool(
            tool_name,
            metadata.category,
            is_read_only=metadata.is_read_only,
            is_destructive=metadata.is_destructive,
        )
        security_audit_log.append(
            build_audit_event(
                thread_id=ctx.thread_id,
                agent_id=ctx.agent_id,
                mode=ctx.mode,
                tool=tool_name,
                category=metadata.category,
                risk_level=risk_level.value,
                decision=result.decision,
                source=result.source,
                reason=result.reason,
                matched_rule=result.matched_rule,
                input_preview=input_preview,
            )
        )
    except Exception as exc:
        logger.warning("Failed to record security audit event for %s: %s", tool_name, exc)


def _classify_auto_permission(tool_name: str, tool_input: dict[str, Any]) -> PermissionResult | None:
    """Use the safety classifier to decide permission in 'auto' mode.

    Returns None if auto mode is not active or classifier unavailable.
    """
    try:
        ctx = get_runtime_context()
        if ctx.mode != "auto":
            return None
    except Exception as e:
        logger.debug("Suppressed error in tool_runtime: %s", e)
        return None
    try:
        from app.agents.safety_classifier import classify_tool_call
        result = classify_tool_call(tool_name, tool_input)
        if result.risk_level == "critical":
            return PermissionResult(
                decision="deny",
                reason=f"Auto-mode classifier: {result.risk_level} (score={result.risk_score})",
                source="classifier",
            )
        if result.requires_confirm:
            return PermissionResult(
                decision="ask",
                reason=f"Auto-mode classifier: {result.risk_level} (score={result.risk_score})",
                source="classifier",
            )
        if result.auto_approve:
            return PermissionResult(
                decision="allow",
                reason=f"Auto-mode classifier: {result.risk_level} (score={result.risk_score})",
                source="classifier",
            )
    except Exception as exc:
        logger.warning("Safety classifier failed for %s: %s", tool_name, exc)
    return None


_ACCEPT_EDITS_TOOL_NAMES = {
    "write_file",
    "patch_skill",
    "edit_skill",
    "rollback_skill",
    "write_skill_file",
}


def _is_plan_safe_shell_command(command: str) -> bool:
    from app.agents.orchestrator import check_bash_safety

    normalized = " ".join((command or "").strip().lower().split())
    if not normalized:
        return False
    safety = check_bash_safety(command)
    if not safety.get("safe", False):
        return False
    if safety.get("is_read_only", False):
        return True
    return any(
        normalized == prefix or normalized.startswith(f"{prefix} ")
        for prefix in ("git status", "git diff", "git log", "git show", "git rev-parse")
    )


def _plan_mode_result(tool_name: str, tool_input: dict[str, Any], metadata: ToolMetadata) -> PermissionResult | None:
    ctx = get_runtime_context()
    if ctx.mode != "plan":
        return None
    if metadata.is_read_only:
        return None
    if tool_name in {"execute_bash", "local_execute_bash", "git_command"}:
        command = ""
        if tool_name in {"execute_bash", "local_execute_bash"}:
            command = str(tool_input.get("command", "")).strip()
        elif tool_name == "git_command":
            command = f"git {str(tool_input.get('command', '')).strip()}".strip()
        if _is_plan_safe_shell_command(command):
            return PermissionResult(
                decision="allow",
                reason="Plan mode allows read-only shell commands",
                source="mode",
            )
    return PermissionResult(
        decision="deny",
        reason=f"Plan mode only allows read-only operations: {tool_name}",
        source="mode",
    )


def _apply_mode_postprocessing(tool_name: str, metadata: ToolMetadata, result: PermissionResult) -> PermissionResult:
    ctx = get_runtime_context()
    if ctx.mode == "dontAsk" and result.decision == "ask":
        return PermissionResult(
            decision="allow",
            reason=f"{result.reason} (auto-approved by dontAsk mode)",
            matched_rule=result.matched_rule,
            source="mode",
        )
    if ctx.mode == "acceptEdits" and result.decision == "ask":
        if metadata.is_read_only or metadata.category in {"file", "search", "utility", "memory", "rag"} or tool_name in _ACCEPT_EDITS_TOOL_NAMES:
            return PermissionResult(
                decision="allow",
                reason=f"{result.reason} (auto-approved by acceptEdits mode)",
                matched_rule=result.matched_rule,
                source="mode",
            )
    return result


def evaluate_tool_permission(tool_name: str, tool_input: dict[str, Any]) -> PermissionResult:
    metadata = get_tool_metadata(tool_name)
    hook_results: list[PermissionResult] = []
    for hook in _PRE_TOOL_HOOKS:
        try:
            result = hook(tool_name, tool_input, metadata)
        except Exception as exc:
            logger.warning("Pre-tool hook failed for %s: %s", tool_name, exc)
            continue
        if result is not None:
            hook_results.append(result)
    ctx = get_runtime_context()
    if ctx.mode == "bypassPermissions" and not any(item.decision == "deny" for item in hook_results):
        final = PermissionResult(
            decision="allow",
            reason=f"Bypassed interactive permission checks for {tool_name}",
            source="mode",
        )
        _append_permission_event(tool_name, tool_input, final)
        return final
    rule_result = _build_rule_result(tool_name, tool_input)
    ordered = []
    if rule_result is not None:
        ordered.append(rule_result)
    ordered.extend(hook_results)

    # Auto mode: use safety classifier before falling through to default
    classifier_result = _classify_auto_permission(tool_name, tool_input)
    if classifier_result is not None:
        ordered.append(classifier_result)

    if ctx.mode == "local" and not any(item.decision == "deny" for item in ordered):
        try:
            from app.local.gateway import local_gateway
            if local_gateway.is_tool_auto_approved("", tool_name):
                final = PermissionResult(
                    decision="allow",
                    reason=f"Auto-approved by local permission settings for {tool_name}",
                    source="local_permissions",
                )
                _append_permission_event(tool_name, tool_input, final)
                return final
        except Exception as e:
            logger.debug("Suppressed error in tool_runtime: %s", e)

    ordered.append(
        PermissionResult(
            decision=metadata.default_permission,
            reason=f"Default permission for {tool_name}",
            source="default",
        )
    )
    final = next((item for item in ordered if item.decision == "deny"), None)
    if final is None:
        final = next((item for item in ordered if item.decision == "ask"), None)
    if final is None:
        final = next((item for item in ordered if item.decision == "allow"), ordered[-1])
    plan_result = _plan_mode_result(tool_name, tool_input, metadata)
    if plan_result is not None:
        final = plan_result
    final = _apply_mode_postprocessing(tool_name, metadata, final)
    if final.decision != "ask":
        _append_permission_event(tool_name, tool_input, final)
    return final


def consume_permission_events() -> list[dict[str, Any]]:
    ctx = get_runtime_context()
    events = ctx.permission_events[ctx.permission_cursor :]
    ctx.permission_cursor = len(ctx.permission_events)
    return events


def consume_hook_events() -> list[dict[str, Any]]:
    """Consume buffered hook events (deny / modified_input) for SSE surfacing."""
    ctx = get_runtime_context()
    events = ctx.hook_events[ctx.hook_cursor :]
    ctx.hook_cursor = len(ctx.hook_events)
    return events


def record_tool_call(tool_name: str, tool_input: dict[str, Any], output: Any):
    ctx = get_runtime_context()
    metadata = get_tool_metadata(tool_name)
    if not metadata.track_usage:
        return
    ctx.tool_calls.append(
        {
            "tool": tool_name,
            "category": metadata.category,
            "input": _preview(tool_input, 180),
            "output": _preview(output, 260),
        }
    )
    record_tool_event(
        tool=tool_name,
        category=metadata.category,
        thread_id=ctx.thread_id,
        agent_id=ctx.agent_id,
        mode=ctx.mode,
        input_preview=_preview(tool_input, 600),
        output_preview=_preview(output, 1000),
        success=True,
        source="agent",
    )
    if len(ctx.tool_calls) > 200:
        ctx.tool_calls = ctx.tool_calls[-100:]
        ctx.tool_summary_cursor = min(ctx.tool_summary_cursor, len(ctx.tool_calls))


def record_tool_event(
    *,
    tool: str,
    category: str = "general",
    thread_id: str = "",
    agent_id: str = "",
    mode: str = "",
    input_preview: str = "",
    output_preview: str = "",
    success: bool = True,
    source: str = "agent",
    client_id: str = "",
):
    event = {
        "event_id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now().isoformat(),
        "tool": tool,
        "category": category,
        "thread_id": thread_id,
        "agent_id": agent_id,
        "mode": mode,
        "input": input_preview,
        "output": output_preview,
        "success": success,
        "source": source,
        "client_id": client_id,
    }
    _TOOL_EVENT_LOG.append(event)
    try:
        TOOL_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TOOL_EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Suppressed error in tool_runtime: %s", e)
    if len(_TOOL_EVENT_LOG) > 1000:
        del _TOOL_EVENT_LOG[:500]


def _load_persisted_tool_events(limit: int = 1000) -> list[dict[str, Any]]:
    if not TOOL_EVENTS_PATH.exists():
        return []
    try:
        lines = TOOL_EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        logger.debug("Suppressed error in tool_runtime: %s", e)
        return []
    events: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                events.append(item)
        except Exception as e:
            logger.debug("Suppressed error in tool_runtime: %s", e)
    return events


def _combined_tool_events() -> list[dict[str, Any]]:
    seen: set[str] = set()
    combined: list[dict[str, Any]] = []
    for event in _load_persisted_tool_events() + _TOOL_EVENT_LOG:
        event_id = str(event.get("event_id") or "")
        dedupe_key = event_id or "|".join([
            str(event.get("timestamp", "")),
            str(event.get("tool", "")),
            str(event.get("thread_id", "")),
            str(event.get("source", "")),
        ])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        combined.append(event)
    combined.sort(key=lambda item: str(item.get("timestamp", "")))
    return combined


def list_tool_events(limit: int = 100, source: str | None = None, thread_id: str | None = None) -> list[dict[str, Any]]:
    events = _combined_tool_events()
    if source:
        events = [event for event in events if event.get("source") == source]
    if thread_id:
        events = [event for event in events if event.get("thread_id") == thread_id]
    return list(reversed(events[-limit:]))


def list_tool_event_threads() -> list[dict[str, Any]]:
    latest_by_thread: dict[str, dict[str, Any]] = {}
    for event in _combined_tool_events():
        thread_id = event.get("thread_id") or ""
        if not thread_id:
            continue
        item = latest_by_thread.setdefault(
            thread_id,
            {
                "thread_id": thread_id,
                "count": 0,
                "latest_at": event.get("timestamp", ""),
                "latest_tool": event.get("tool", ""),
            },
        )
        item["count"] += 1
        item["latest_at"] = event.get("timestamp", item["latest_at"])
        item["latest_tool"] = event.get("tool", item["latest_tool"])
    return sorted(latest_by_thread.values(), key=lambda item: item.get("latest_at", ""), reverse=True)


def _normalize_tool_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, (dict, list)):
        return json.dumps(output, ensure_ascii=False)
    return str(output)


def _call_sync_tool(tool, tool_input: dict[str, Any]):
    func = getattr(tool, "func", None)
    if callable(func):
        return func(**tool_input)
    coroutine = getattr(tool, "coroutine", None)
    if callable(coroutine):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine(**tool_input))
        raise RuntimeError(f"Tool '{tool.name}' requires async invocation")
    return tool.invoke(tool_input)


async def _call_async_tool(tool, tool_input: dict[str, Any]):
    if hasattr(tool, "ainvoke"):
        return await tool.ainvoke(tool_input)
    return tool.invoke(tool_input)


def _fire_pre_tool_use_hooks(tool_name: str, kwargs: dict) -> tuple[bool, str, dict]:
    """Fire PreToolUse hooks synchronously. Returns (allowed, reason, maybe_modified_kwargs).

    Claude Code semantics:
      - If any hook returns decision="deny", block the tool call.
      - If any hook returns a `modified_input` dict, use that as the new kwargs.
    Results are buffered into RuntimeContext.hook_events so super_agent.py
    can surface them via SSE without double-firing hooks.
    """
    try:
        from app.agents.hooks import hooks_registry
        ctx = get_runtime_context()
        results = hooks_registry.fire_sync("PreToolUse", {
            "tool_name": tool_name,
            "tool_input": kwargs,
        })
        for r in results or []:
            if r.decision == "deny":
                ctx.hook_events.append({
                    "type": "hook_deny", "tool": tool_name,
                    "hook": r.hook_name, "reason": r.reason or "Denied by hook",
                })
                return False, r.reason or "Denied by hook", kwargs
            if r.modified_input and isinstance(r.modified_input, dict):
                ctx.hook_events.append({
                    "type": "hook_modified_input", "tool": tool_name,
                    "hook": r.hook_name,
                    "original_keys": list(kwargs.keys()),
                    "modified_keys": list(r.modified_input.keys()),
                })
                kwargs = r.modified_input
        return True, "", kwargs
    except Exception as e:
        logger.debug("Suppressed error in tool_runtime: %s", e)
        return True, "", kwargs


def _fire_post_tool_use_hooks(tool_name: str, kwargs: dict, output: Any) -> None:
    try:
        from app.agents.hooks import hooks_registry
        hooks_registry.fire_sync("PostToolUse", {
            "tool_name": tool_name,
            "tool_input": kwargs,
            "output": str(output)[:1000],
        })
    except Exception as e:
        logger.debug("Suppressed error in tool_runtime: %s", e)


def wrap_langchain_tool(tool):
    if getattr(tool, "_sap_wrapped", False):
        return tool
    metadata = get_tool_metadata(tool.name)
    args_schema = getattr(tool, "args_schema", None)
    return_direct = getattr(tool, "return_direct", False)

    def _call(**kwargs):
        # --- PreToolUse hook (Claude Code pattern): supports deny + modified_input ---
        allowed, reason, kwargs = _fire_pre_tool_use_hooks(tool.name, kwargs)
        if not allowed:
            return f"Permission denied by hook for tool '{tool.name}': {reason}"

        decision = evaluate_tool_permission(tool.name, kwargs)
        if decision.decision == "deny":
            return f"Permission denied for tool '{tool.name}': {decision.reason}"
        if decision.decision == "ask":
            ctx = get_runtime_context()
            decision = asyncio.run(
                permission_requests.request_permission(
                    thread_id=ctx.thread_id,
                    agent_id=ctx.agent_id,
                    mode=ctx.mode,
                    tool_name=tool.name,
                    tool_input=kwargs,
                    reason=decision.reason,
                    source=decision.source,
                )
            )
            _append_permission_event(tool.name, kwargs, decision)
            if decision.decision != "allow":
                return f"Permission denied for tool '{tool.name}': {decision.reason}"
        output = _call_sync_tool(tool, kwargs)
        normalized = _normalize_tool_output(output)
        record_tool_call(tool.name, kwargs, normalized)
        _fire_post_tool_use_hooks(tool.name, kwargs, normalized)
        return normalized

    async def _acall(**kwargs):
        # --- PreToolUse hook (Claude Code pattern) ---
        allowed, reason, kwargs = _fire_pre_tool_use_hooks(tool.name, kwargs)
        if not allowed:
            return f"Permission denied by hook for tool '{tool.name}': {reason}"

        decision = evaluate_tool_permission(tool.name, kwargs)
        if decision.decision == "deny":
            return f"Permission denied for tool '{tool.name}': {decision.reason}"
        if decision.decision == "ask":
            ctx = get_runtime_context()
            decision = await permission_requests.request_permission(
                thread_id=ctx.thread_id,
                agent_id=ctx.agent_id,
                mode=ctx.mode,
                tool_name=tool.name,
                tool_input=kwargs,
                reason=decision.reason,
                source=decision.source,
            )
            _append_permission_event(tool.name, kwargs, decision)
            if decision.decision != "allow":
                return f"Permission denied for tool '{tool.name}': {decision.reason}"
        output = await _call_async_tool(tool, kwargs)
        normalized = _normalize_tool_output(output)
        record_tool_call(tool.name, kwargs, normalized)
        _fire_post_tool_use_hooks(tool.name, kwargs, normalized)
        return normalized

    kwargs: dict[str, Any] = {
        "func": _call,
        "coroutine": _acall,
        "name": tool.name,
        "description": tool.description,
        "return_direct": return_direct,
        "infer_schema": args_schema is None,
    }
    if args_schema is not None:
        kwargs["args_schema"] = args_schema
    wrapped = StructuredTool.from_function(**kwargs)
    setattr(wrapped, "_sap_wrapped", True)
    return wrapped


def _unique_preserve_order(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def summarize_tool_names(tool_names: list[str]) -> str:
    unique = _unique_preserve_order(tool_names)
    if not unique:
        return "No tool activity"
    if any(name.startswith("local_") for name in unique):
        return "Worked with local computer tools"
    if any(name in {"write_file", "local_write_file"} for name in unique) and any(
        name in {"execute_python", "execute_javascript", "execute_bash", "git_command"}
        for name in unique
    ):
        return "Ran code and updated files"
    if any(name in {"web_search", "web_fetch", "summarize_url", "http_request"} for name in unique) and any(
        name in {"read_file", "list_files"} for name in unique
    ):
        return "Researched sources and inspected files"
    if all(name in {"web_search", "web_fetch", "summarize_url", "http_request"} for name in unique):
        return "Researched external sources"
    if all(name in {"read_file", "list_files", "pdf_extract"} for name in unique):
        return "Inspected files and documents"
    if all(name in {"execute_python", "execute_javascript", "execute_bash", "git_command"} for name in unique):
        return "Ran code and shell commands"
    first = unique[0]
    if len(unique) == 1:
        return f"Used {first}"
    return f"Used {unique[0]} + {unique[1]}"


def consume_tool_use_summary() -> str | None:
    ctx = get_runtime_context()
    new_calls = ctx.tool_calls[ctx.tool_summary_cursor :]
    if not new_calls:
        return None
    ctx.tool_summary_cursor = len(ctx.tool_calls)
    return summarize_tool_names([call["tool"] for call in new_calls])


def emit_file_diff(diff_payload: dict[str, Any]) -> None:
    """Buffer a file_diff event for the agent loop to pick up and stream to the client."""
    _FILE_DIFF_BUFFER.append(diff_payload)


def consume_file_diffs() -> list[dict[str, Any]]:
    """Drain all buffered file_diff events. Called by the agent loop after each tool execution."""
    diffs = list(_FILE_DIFF_BUFFER)
    _FILE_DIFF_BUFFER.clear()
    return diffs


def summarize_agent_progress(task: str, tool_calls: list[dict[str, Any]] | None = None, status: str = "running") -> str:
    task_preview = task[:60].strip()
    if tool_calls:
        tool_names = [call.get("tool", "") for call in tool_calls if call.get("tool")]
        recent = _unique_preserve_order(tool_names[-3:])
        summary = summarize_tool_names(tool_names)
        if status == "completed":
            return f"Finished {task_preview} via {summary.lower()}"
        if status == "failed":
            return f"Failed {task_preview} after using {', '.join(recent)}"
        return f"Working on {task_preview} via {', '.join(recent)}"
    if status == "failed":
        return f"Failed on {task_preview}"
    if status == "completed":
        return f"Finished {task_preview}"
    return f"Working on {task_preview}"
