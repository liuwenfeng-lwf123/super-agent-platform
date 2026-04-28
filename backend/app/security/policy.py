from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    SAFE = "safe"
    APPROVAL_REQUIRED = "approval_required"
    DANGEROUS = "dangerous"
    DISABLED = "disabled"


class SecurityDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class SecurityPolicyResult:
    decision: SecurityDecision
    risk_level: RiskLevel
    reason: str
    source: str = "security_policy"


SAFE_CATEGORIES = {"utility", "memory", "rag", "audit", "discovery"}
APPROVAL_CATEGORIES = {"file", "network", "system", "mcp", "custom", "evolution", "hooks", "plugins", "cron", "agents"}
DANGEROUS_CATEGORIES = {"execution", "local"}

SAFE_TOOLS = {
    "read_file",
    "list_files",
    "get_current_time",
    "calculate",
    "system_info",
    "browser_get_state",
    "browser_extract_text",
    "knowledge_search",
    "session_search",
    "tool_search",
    "file_history",
    "local_read_file",
    "local_list_files",
    "local_get_system_info",
    "local_read_clipboard",
    "local_search_code",
    "local_project_index",
    "local_list_schedules",
}

APPROVAL_TOOLS = {
    "web_search",
    "web_fetch",
    "summarize_url",
    "http_request",
    "write_file",
    "screenshot",
    "clipboard_read",
    "clipboard_write",
    "open_app",
    "open_url",
    "browser_open",
    "browser_run_javascript",
    "browser_click",
    "browser_fill",
    "notify",
    "create_tool",
    "remove_custom_tool",
    "create_skill",
    "patch_skill",
    "edit_skill",
    "rollback_skill",
    "write_skill_file",
    "remove_skill_file",
    "gepa_evolve",
    "semantic_check",
    "spawn_agent",
    "send_agent_message",
    "register_hook",
    "fire_hook",
    "manage_plugin",
    "manage_cron",
    "run_discovered_tool",
    "local_write_file",
    "local_edit_file",
    "local_undo_edit",
    "local_upload_to_workspace",
    "local_download_from_workspace",
    "local_write_clipboard",
    "local_send_notification",
    "local_manage_window",
    "local_git",
    "local_create_schedule",
    "local_delete_schedule",
}

DANGEROUS_TOOLS = {
    "execute_bash",
    "execute_python",
    "execute_javascript",
    "git_command",
    "execute_code",
    "local_execute_bash",
    "local_execute_python",
    "local_open_app",
}

PRODUCTION_DISABLED_TOOLS = {
    "execute_bash",
    "execute_python",
    "execute_javascript",
    "execute_code",
    "git_command",
    "local_execute_bash",
    "local_execute_python",
    "local_open_app",
    "open_app",
    "clipboard_write",
    "browser_run_javascript",
    "create_tool",
    "remove_custom_tool",
    "create_skill",
    "patch_skill",
    "edit_skill",
    "rollback_skill",
    "write_skill_file",
    "remove_skill_file",
    "gepa_evolve",
    "manage_plugin",
    "manage_cron",
}


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def app_environment() -> str:
    return os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower() or "development"


def is_production_environment() -> bool:
    return app_environment() in {"prod", "production"}


def production_policy_enabled() -> bool:
    return _env_flag("ENABLE_PRODUCTION_SECURITY_POLICY", True)


def disabled_tools() -> set[str]:
    values = os.getenv("SECURITY_DISABLED_TOOLS", "")
    configured = {item.strip() for item in values.split(",") if item.strip()}
    if is_production_environment() and production_policy_enabled():
        return PRODUCTION_DISABLED_TOOLS | configured
    return configured


def classify_tool(tool_name: str, category: str = "general", is_read_only: bool = False, is_destructive: bool = False) -> RiskLevel:
    if tool_name in disabled_tools():
        return RiskLevel.DISABLED
    if tool_name in DANGEROUS_TOOLS:
        return RiskLevel.DANGEROUS
    if tool_name in SAFE_TOOLS or is_read_only or category in SAFE_CATEGORIES:
        return RiskLevel.SAFE
    if tool_name in APPROVAL_TOOLS or is_destructive or category in APPROVAL_CATEGORIES:
        return RiskLevel.APPROVAL_REQUIRED
    if category in DANGEROUS_CATEGORIES:
        return RiskLevel.DANGEROUS
    return RiskLevel.APPROVAL_REQUIRED


def evaluate_tool_security(
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    *,
    category: str = "general",
    is_read_only: bool = False,
    is_destructive: bool = False,
    mode: str = "standard",
) -> SecurityPolicyResult | None:
    risk = classify_tool(tool_name, category, is_read_only, is_destructive)
    if risk == RiskLevel.DISABLED:
        return SecurityPolicyResult(
            decision=SecurityDecision.DENY,
            risk_level=risk,
            reason=f"Tool disabled by security policy: {tool_name}",
        )
    if not is_production_environment() or not production_policy_enabled():
        return None
    if risk == RiskLevel.DANGEROUS:
        return SecurityPolicyResult(
            decision=SecurityDecision.DENY,
            risk_level=risk,
            reason=f"Dangerous tool disabled in production: {tool_name}",
        )
    if risk == RiskLevel.APPROVAL_REQUIRED:
        return SecurityPolicyResult(
            decision=SecurityDecision.ASK,
            risk_level=risk,
            reason=f"Production approval required for {tool_name}",
        )
    return SecurityPolicyResult(
        decision=SecurityDecision.ALLOW,
        risk_level=risk,
        reason=f"Production security policy allows read-only tool: {tool_name}",
    )


def permission_matrix() -> dict[str, list[str]]:
    return {
        RiskLevel.SAFE.value: sorted(SAFE_TOOLS),
        RiskLevel.APPROVAL_REQUIRED.value: sorted(APPROVAL_TOOLS),
        RiskLevel.DANGEROUS.value: sorted(DANGEROUS_TOOLS),
        RiskLevel.DISABLED.value: sorted(PRODUCTION_DISABLED_TOOLS),
    }
