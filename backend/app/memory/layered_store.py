"""
Layered Memory Store — inspired by Claude Code's multi-layer memory system.

Layers:
  1. Project memory (MEMORY.md) — shared across all users/agents in a project
  2. User memory (user_memory.json) — per-user preferences, style, context
  3. Agent memory (agent_memory/{agent_type}.json) — per-agent-type persistent state
  4. Session memory (ephemeral) — extracted from conversation, not persisted

Constraints (from Claude Code):
  - MEMORY.md: max 200 lines / 25KB
  - Each agent memory file: max 50 entries
  - Auto-extraction of memories from conversation
"""
import json
import os
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

MAX_PROJECT_MEMORY_LINES = 200
MAX_PROJECT_MEMORY_BYTES = 25 * 1024  # 25KB
MAX_AGENT_MEMORY_ENTRIES = 50


def _scan_memory_content(content: str) -> tuple[bool, list[str]]:
    """Scan memory content for prompt injection / exfiltration threats."""
    import re
    MEMORY_THREAT_PATTERNS = [
        r"ignore.*previous.*instructions",
        r"you\s+are\s+now",
        r"system\s*:\s*",
        r"<\|im_start\|>",
        r"\[INST\]",
        r"(api[_-]?key|password|secret|token)\s*[:=]\s*[\w\-]+",
        r"curl.*\|\s*bash",
        r"authorized_keys",
        r"[\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]",
    ]
    threats = []
    for pattern in MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            threats.append(f"Blocked pattern: {pattern[:40]}")
    return len(threats) == 0, threats


class LayeredMemoryStore:
    """Multi-layer memory system with project, user, and agent scopes.
    Includes USER.md (Hermes-style user profile) and security scanning."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or settings.memory_dir
        self._session_memories: dict[str, list[dict]] = {}
        os.makedirs(self.base_dir, exist_ok=True)
        os.makedirs(os.path.join(self.base_dir, "agent_memory"), exist_ok=True)

    # =========================================================================
    # Layer 1: Project Memory (MEMORY.md — shared, human-readable index)
    # =========================================================================
    @property
    def _project_memory_path(self) -> str:
        return os.path.join(self.base_dir, "MEMORY.md")

    def get_project_memory(self) -> str:
        """Read project memory (MEMORY.md). Returns content, truncated to limit."""
        if not os.path.exists(self._project_memory_path):
            return ""
        content = Path(self._project_memory_path).read_text(encoding="utf-8")
        return self._truncate_project_memory(content)

    def update_project_memory(self, content: str) -> dict:
        """Write project memory. Enforces size limits and security scanning."""
        safe, threats = _scan_memory_content(content)
        if not safe:
            return {"error": f"Memory blocked by security scan: {'; '.join(threats)}"}
        content = self._truncate_project_memory(content)
        Path(self._project_memory_path).write_text(content, encoding="utf-8")
        lines = content.strip().split("\n") if content.strip() else []
        return {"lines": len(lines), "bytes": len(content.encode("utf-8")), "truncated": len(lines) >= MAX_PROJECT_MEMORY_LINES}

    def append_project_memory(self, entry: str) -> dict:
        """Append a line to MEMORY.md if within limits. Rejects exact duplicates."""
        safe, threats = _scan_memory_content(entry)
        if not safe:
            return {"error": f"Memory entry blocked: {'; '.join(threats)}"}
        current = self.get_project_memory()
        # Duplicate detection (Hermes pattern)
        if entry.strip() in current:
            return {"success": True, "duplicate": True, "message": "Entry already exists, no duplicate added"}
        lines = current.strip().split("\n") if current.strip() else []
        if len(lines) >= MAX_PROJECT_MEMORY_LINES:
            return {"error": f"Project memory full ({len(lines)}/{MAX_PROJECT_MEMORY_LINES} lines)"}
        lines.append(entry.strip())
        return self.update_project_memory("\n".join(lines))

    def replace_project_memory(self, old_text: str, new_text: str) -> dict:
        """Replace a memory entry using substring matching (Hermes pattern)."""
        safe, threats = _scan_memory_content(new_text)
        if not safe:
            return {"error": f"Replacement blocked: {'; '.join(threats)}"}
        current = self.get_project_memory()
        if old_text.strip() not in current:
            return {"error": "old_text not found in memory"}
        updated = current.replace(old_text.strip(), new_text.strip(), 1)
        return self.update_project_memory(updated)

    def remove_project_memory(self, old_text: str) -> dict:
        """Remove a memory entry using substring matching (Hermes pattern)."""
        current = self.get_project_memory()
        if old_text.strip() not in current:
            return {"error": "old_text not found in memory"}
        updated = current.replace(old_text.strip(), "", 1)
        # Clean up double blank lines
        while "\n\n\n" in updated:
            updated = updated.replace("\n\n\n", "\n\n")
        return self.update_project_memory(updated.strip())

    def check_duplicate(self, entry: str) -> bool:
        """Check if an exact entry already exists in project memory."""
        current = self.get_project_memory()
        return entry.strip() in current

    def get_memory_capacity(self) -> dict:
        """Return capacity usage with percentage (Hermes-style)."""
        current = self.get_project_memory()
        lines = current.strip().split("\n") if current.strip() else []
        byte_count = len(current.encode("utf-8"))
        line_pct = min(100, round(len(lines) / MAX_PROJECT_MEMORY_LINES * 100))
        byte_pct = min(100, round(byte_count / MAX_PROJECT_MEMORY_BYTES * 100))
        return {
            "lines": len(lines),
            "max_lines": MAX_PROJECT_MEMORY_LINES,
            "bytes": byte_count,
            "max_bytes": MAX_PROJECT_MEMORY_BYTES,
            "line_pct": line_pct,
            "byte_pct": byte_pct,
            "display": f"[{max(line_pct, byte_pct)}% — {byte_count:,}/{MAX_PROJECT_MEMORY_BYTES:,} chars]",
        }

    def _truncate_project_memory(self, content: str) -> str:
        lines = content.split("\n")
        if len(lines) > MAX_PROJECT_MEMORY_LINES:
            lines = lines[:MAX_PROJECT_MEMORY_LINES]
            lines.append(f"<!-- Truncated: {MAX_PROJECT_MEMORY_LINES} line limit reached -->")
        result = "\n".join(lines)
        if len(result.encode("utf-8")) > MAX_PROJECT_MEMORY_BYTES:
            while len(result.encode("utf-8")) > MAX_PROJECT_MEMORY_BYTES and lines:
                lines.pop()
            result = "\n".join(lines)
        return result

    # =========================================================================
    # Layer 2a: USER.md — Hermes-style user profile (dedicated file)
    # =========================================================================
    @property
    def _user_profile_path(self) -> str:
        return os.path.join(self.base_dir, "USER.md")

    def get_user_profile(self) -> str:
        """Read USER.md — dedicated user profile (Hermes pattern)."""
        if not os.path.exists(self._user_profile_path):
            return ""
        return Path(self._user_profile_path).read_text(encoding="utf-8")

    def update_user_profile(self, content: str) -> dict:
        """Update USER.md with security scanning."""
        safe, threats = _scan_memory_content(content)
        if not safe:
            return {"error": f"User profile blocked: {'; '.join(threats)}"}
        # Limit to 100 lines
        lines = content.split("\n")
        if len(lines) > 100:
            lines = lines[:100]
            content = "\n".join(lines)
        Path(self._user_profile_path).write_text(content, encoding="utf-8")
        return {"lines": len(lines), "bytes": len(content.encode("utf-8"))}

    def append_user_profile(self, entry: str) -> dict:
        """Append to USER.md."""
        safe, threats = _scan_memory_content(entry)
        if not safe:
            return {"error": f"Profile entry blocked: {'; '.join(threats)}"}
        current = self.get_user_profile()
        lines = current.strip().split("\n") if current.strip() else []
        if len(lines) >= 100:
            return {"error": "User profile full (100 lines)"}
        lines.append(entry.strip())
        return self.update_user_profile("\n".join(lines))

    # =========================================================================
    # Layer 2b: User Memory (per-user preferences and context)
    # =========================================================================
    def _user_memory_path(self, user_id: str = "default") -> str:
        safe_name = user_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        return os.path.join(self.base_dir, f"user_{safe_name}.json")

    def get_user_memory(self, user_id: str = "default") -> dict:
        path = self._user_memory_path(user_id)
        if not os.path.exists(path):
            return {"preferences": {}, "context": {}, "updated_at": None}
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Suppressed error in layered_store: %s", e)
            return {"preferences": {}, "context": {}, "updated_at": None}

    def set_user_memory(self, key: str, value: str, category: str = "context", user_id: str = "default") -> dict:
        safe, threats = _scan_memory_content(value)
        if not safe:
            return {"error": f"Memory value blocked: {'; '.join(threats)}"}
        data = self.get_user_memory(user_id)
        if category not in data:
            data[category] = {}
        data[category][key] = value
        data["updated_at"] = datetime.now().isoformat()
        Path(self._user_memory_path(user_id)).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return data

    def remove_user_memory(self, key: str, category: str = "context", user_id: str = "default") -> dict:
        data = self.get_user_memory(user_id)
        values = data.get(category)
        if not isinstance(values, dict) or key not in values:
            return {"error": "Entry not found"}
        del values[key]
        data["updated_at"] = datetime.now().isoformat()
        Path(self._user_memory_path(user_id)).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"deleted": True}

    # =========================================================================
    # Layer 3: Agent Memory (per-agent-type persistent state)
    # =========================================================================
    def _agent_memory_path(self, agent_type: str) -> str:
        safe_name = agent_type.lower().replace(" ", "_").replace("/", "_")
        return os.path.join(self.base_dir, "agent_memory", f"{safe_name}.json")

    def get_agent_memory(self, agent_type: str) -> list[dict]:
        path = self._agent_memory_path(agent_type)
        if not os.path.exists(path):
            return []
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Suppressed error in layered_store: %s", e)
            return []

    def add_agent_memory(self, agent_type: str, key: str, value: str, category: str = "learned") -> dict:
        entries = self.get_agent_memory(agent_type)

        # Update existing or add new
        for entry in entries:
            if entry.get("key") == key:
                entry["value"] = value
                entry["updated_at"] = datetime.now().isoformat()
                entry["access_count"] = entry.get("access_count", 0) + 1
                self._save_agent_memory(agent_type, entries)
                return {"updated": True, "total": len(entries)}

        # Enforce limit
        if len(entries) >= MAX_AGENT_MEMORY_ENTRIES:
            # Evict least-accessed entry
            entries.sort(key=lambda e: e.get("access_count", 0))
            evicted = entries.pop(0)
            logger.info(f"Agent memory eviction ({agent_type}): {evicted.get('key')}")

        entries.append({
            "key": key,
            "value": value,
            "category": category,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "access_count": 0,
        })
        self._save_agent_memory(agent_type, entries)
        return {"added": True, "total": len(entries)}

    def search_agent_memory(self, agent_type: str, query: str) -> list[dict]:
        entries = self.get_agent_memory(agent_type)
        q = query.lower()
        results = [e for e in entries if q in e.get("key", "").lower() or q in e.get("value", "").lower()]
        # Bump access count
        for r in results:
            r["access_count"] = r.get("access_count", 0) + 1
        if results:
            self._save_agent_memory(agent_type, entries)
        return results

    def _save_agent_memory(self, agent_type: str, entries: list[dict]):
        filepath = self._agent_memory_path(agent_type)
        dir_name = os.path.dirname(filepath)
        os.makedirs(dir_name, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, filepath)
        except Exception as e:
            logger.debug("Suppressed error in layered_store: %s", e)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def remove_agent_memory(self, agent_type: str, key: str) -> dict:
        entries = self.get_agent_memory(agent_type)
        next_entries = [entry for entry in entries if entry.get("key") != key]
        if len(next_entries) == len(entries):
            return {"error": "Entry not found"}
        self._save_agent_memory(agent_type, next_entries)
        return {"deleted": True}

    def add_session_memory(self, session_id: str, content: str, category: str = "observation"):
        if session_id not in self._session_memories:
            self._session_memories[session_id] = []
        self._session_memories[session_id].append({
            "content": content,
            "category": category,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep only last 50 per session
        self._session_memories[session_id] = self._session_memories[session_id][-50:]

    def get_session_memories(self, session_id: str) -> list[dict]:
        return self._session_memories.get(session_id, [])

    def list_session_memories(self) -> dict[str, list[dict]]:
        return self._session_memories

    def clear_session_memories(self, session_id: str):
        self._session_memories.pop(session_id, None)

    # =========================================================================
    # Unified context builder (merges all layers)
    # =========================================================================
    def build_context(self, query: str = "", agent_type: str = "", user_id: str = "default", session_id: str = "") -> str:
        """Build unified memory context from all layers for injection into system prompt."""
        sections = []

        # Project memory
        proj = self.get_project_memory()
        if proj:
            sections.append(f"## Project Memory\n{proj}")

        # User profile (USER.md)
        user_profile = self.get_user_profile()
        if user_profile:
            sections.append(f"## User Profile\n{user_profile}")

        # User memory
        user = self.get_user_memory(user_id)
        user_ctx = user.get("context", {})
        user_pref = user.get("preferences", {})
        if user_ctx or user_pref:
            lines = []
            for k, v in user_pref.items():
                lines.append(f"- Preference: {k} = {v}")
            for k, v in user_ctx.items():
                lines.append(f"- {k}: {v}")
            sections.append(f"## User Context\n" + "\n".join(lines))

        # Agent memory
        if agent_type and query:
            agent_mems = self.search_agent_memory(agent_type, query)
            if agent_mems:
                lines = [f"- {m['key']}: {m['value']}" for m in agent_mems[:5]]
                sections.append(f"## Agent Memory ({agent_type})\n" + "\n".join(lines))

        # Session memory
        if session_id:
            sess = self.get_session_memories(session_id)
            if sess:
                lines = [f"- {m['content']}" for m in sess[-10:]]
                sections.append(f"## Session Context\n" + "\n".join(lines))

        return "\n\n".join(sections) if sections else ""

    def get_stats(self) -> dict:
        """Return memory stats for API/debug."""
        proj_content = self.get_project_memory()
        proj_lines = len(proj_content.strip().split("\n")) if proj_content.strip() else 0

        agent_dir = os.path.join(self.base_dir, "agent_memory")
        agent_files = [f for f in os.listdir(agent_dir) if f.endswith(".json")] if os.path.isdir(agent_dir) else []

        return {
            "project_memory": {
                "lines": proj_lines,
                "max_lines": MAX_PROJECT_MEMORY_LINES,
                "bytes": len(proj_content.encode("utf-8")),
                "max_bytes": MAX_PROJECT_MEMORY_BYTES,
            },
            "agent_memories": len(agent_files),
            "agent_types": [f.replace(".json", "") for f in agent_files],
            "active_sessions": len(self._session_memories),
        }


    # =========================================================================
    # Auto Memory: multi-topic file storage (Claude Code pattern)
    # =========================================================================
    @property
    def _auto_memory_dir(self) -> str:
        d = os.path.join(self.base_dir, "auto_memory")
        os.makedirs(d, exist_ok=True)
        return d

    def save_auto_memory(self, topic: str, content: str) -> dict:
        """Save topic-specific memory to a dedicated file (Claude Code auto-memory pattern)."""
        safe, threats = _scan_memory_content(content)
        if not safe:
            return {"error": f"Blocked: {'; '.join(threats)}"}
        safe_topic = topic.replace("/", "_").replace("\\", "_").replace("..", "_")
        path = os.path.join(self._auto_memory_dir, f"{safe_topic}.md")
        Path(path).write_text(content, encoding="utf-8")
        return {"topic": topic, "bytes": len(content.encode("utf-8"))}

    def get_auto_memory(self, topic: str) -> str:
        """Read a topic-specific auto memory file."""
        safe_topic = topic.replace("/", "_").replace("\\", "_").replace("..", "_")
        path = os.path.join(self._auto_memory_dir, f"{safe_topic}.md")
        if not os.path.exists(path):
            return ""
        return Path(path).read_text(encoding="utf-8")

    def list_auto_memories(self) -> list[dict]:
        """List all topic files in auto memory."""
        results = []
        for f in Path(self._auto_memory_dir).glob("*.md"):
            results.append({
                "topic": f.stem,
                "bytes": f.stat().st_size,
            })
        return results

    def build_auto_memory_context(self) -> str:
        """Build concatenated auto memory for system prompt (index file + topics)."""
        index_path = os.path.join(self._auto_memory_dir, "MEMORY.md")
        if os.path.exists(index_path):
            return Path(index_path).read_text(encoding="utf-8")
        # Fallback: concat all topic files
        topics = self.list_auto_memories()
        if not topics:
            return ""
        lines = ["## Auto Memory (accumulated across sessions)"]
        for t in topics[:10]:
            content = self.get_auto_memory(t["topic"])
            lines.append(f"### {t['topic']}\n{content[:500]}")
        return "\n\n".join(lines)

    # =========================================================================
    # CLAUDE.md multi-level loading (Claude Code pattern)
    # =========================================================================
    CLAUDE_MD_SEARCH_DIRS = [
        ".",           # project root
        ".claude",     # .claude/ directory
    ]

    def load_claude_md(self, project_root: str = "") -> str:
        """Load and merge CLAUDE.md files from multiple levels (Claude Code pattern).
        Priority: system → user → project → subdirectory"""
        sections = []

        # User-level (~/.claude/CLAUDE.md equivalent)
        user_claude = os.path.join(self.base_dir, "CLAUDE.md")
        if os.path.exists(user_claude):
            sections.append(f"# User-level CLAUDE.md\n{Path(user_claude).read_text(encoding='utf-8')}")

        # Project-level
        if project_root:
            for search_dir in self.CLAUDE_MD_SEARCH_DIRS:
                candidate = os.path.join(project_root, search_dir, "CLAUDE.md")
                if os.path.exists(candidate) and candidate != user_claude:
                    sections.append(f"# Project CLAUDE.md ({search_dir})\n{Path(candidate).read_text(encoding='utf-8')}")

        # Also load .claude/rules/ directory
        if project_root:
            rules_dir = os.path.join(project_root, ".claude", "rules")
            if os.path.isdir(rules_dir):
                for rule_file in sorted(Path(rules_dir).glob("*.md")):
                    sections.append(f"# Rule: {rule_file.stem}\n{rule_file.read_text(encoding='utf-8')}")

        return "\n\n".join(sections) if sections else ""

    def load_path_rules(self, project_root: str, file_path: str) -> str:
        """Load path-scoped rules from .claude/rules/ (Claude Code pattern).
        Rules that match the file path are loaded."""
        if not project_root:
            return ""
        rules_dir = os.path.join(project_root, ".claude", "rules")
        if not os.path.isdir(rules_dir):
            return ""

        matched = []
        for rule_file in sorted(Path(rules_dir).glob("*.md")):
            content = rule_file.read_text(encoding="utf-8")
            # Check if rule has path scope (first line contains paths:)
            first_line = content.split("\n")[0].lower()
            if first_line.startswith("paths:"):
                paths = first_line.replace("paths:", "").strip().split(",")
                if any(p.strip() in file_path for p in paths):
                    matched.append(content)
            else:
                # No path scope = applies everywhere
                matched.append(content)
        return "\n\n".join(matched)


# Singleton
layered_memory = LayeredMemoryStore()
