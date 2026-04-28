"""Permission rule scope layering (Claude Code pattern).

Claude Code has FOUR configuration scopes, merged in order with precedence:

  1. Managed — organization-controlled, deployed via system-level mechanisms.
     Highest priority; cannot be overridden by lower scopes.
     Location (mac/linux): /etc/hermes/managed_permissions.json
                    (win): %ProgramData%/hermes/managed_permissions.json

  2. User    — personal defaults that apply across all projects.
     Location: ~/.hermes/permissions.json (or ~/.claude/permissions.json for compat).

  3. Project — team-shared, checked into Git.
     Location: <cwd>/.hermes/permissions.json (or <cwd>/.claude/permissions.json).

  4. Local   — repo-specific personal overrides; NOT committed.
     Location: <cwd>/.hermes/permissions.local.json.

Merge rules inside a scope or across scopes (first match wins, evaluated top-down):
  deny  >  ask  >  allow

Managed scope rules CANNOT be overridden: a managed `deny` blocks any lower-scope
`allow` for the same pattern. This is the Claude Code "auto-mode rule demotion"
principle applied to rule precedence.
"""
from __future__ import annotations

import json
import logging
import os
import platform
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


SCOPE_ORDER = ("managed", "user", "project", "local")
SCOPE_PRIORITY = {name: i for i, name in enumerate(SCOPE_ORDER)}


def _managed_path() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        return Path(base) / "hermes" / "managed_permissions.json"
    return Path("/etc/hermes/managed_permissions.json")


def _user_paths() -> list[Path]:
    return [
        Path.home() / ".hermes" / "permissions.json",
        Path.home() / ".claude" / "permissions.json",
    ]


def _project_paths() -> list[Path]:
    cwd = Path.cwd()
    return [
        cwd / ".hermes" / "permissions.json",
        cwd / ".claude" / "permissions.json",
    ]


def _local_paths() -> list[Path]:
    cwd = Path.cwd()
    return [
        cwd / ".hermes" / "permissions.local.json",
        cwd / ".claude" / "permissions.local.json",
    ]


def _load_json_safe(path: Path) -> Optional[dict]:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load permissions at %s: %s", path, e)
        return None


def _load_scope(scope: str) -> Optional[dict]:
    """Load the first existing file for a given scope (returns normalized dict)."""
    if scope == "managed":
        paths = [_managed_path()]
    elif scope == "user":
        paths = _user_paths()
    elif scope == "project":
        paths = _project_paths()
    elif scope == "local":
        paths = _local_paths()
    else:
        return None

    for path in paths:
        data = _load_json_safe(path)
        if data is not None:
            return _normalize(data)
    return None


def _normalize(data: dict) -> dict:
    """Ensure the three rule arrays exist with string values."""
    out = {
        "always_allow": [],
        "always_deny": [],
        "always_ask": [],
    }
    for key in out:
        values = data.get(key, []) if isinstance(data, dict) else []
        out[key] = [str(v).strip() for v in values if str(v).strip()]
    return out


def load_layered_rules() -> tuple[dict, list[dict]]:
    """Merge all four scopes.

    Returns:
        merged_rules: dict {always_allow, always_deny, always_ask} (de-duped)
        scope_detail: list of per-scope info [{name, loaded, path_tried, rules}]

    Merge policy:
      - Managed rules come first in each list (cannot be overridden).
      - Within a scope, rules are kept in their declared order.
      - Across scopes, the order is Managed → User → Project → Local.
      - When `match()` scans rules, first match wins (deny > ask > allow).
    """
    merged = {"always_allow": [], "always_deny": [], "always_ask": []}
    scope_detail: list[dict] = []
    seen: dict[str, set[str]] = {k: set() for k in merged}

    for scope in SCOPE_ORDER:
        rules = _load_scope(scope)
        info = {
            "scope": scope,
            "loaded": rules is not None,
            "rules": rules or {},
        }
        scope_detail.append(info)
        if not rules:
            continue
        for bucket in merged:
            for pattern in rules.get(bucket, []):
                if pattern in seen[bucket]:
                    continue
                seen[bucket].add(pattern)
                merged[bucket].append(pattern)
    return merged, scope_detail


def get_effective_rules() -> dict:
    """Return just the merged rule dict (convenience for PermissionRuleStore)."""
    merged, _ = load_layered_rules()
    return merged
