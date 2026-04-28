"""
Hermes-inspired Learning Loop — Closed-loop self-improvement.

Core mechanisms borrowed from Hermes Agent:
1. Periodic Nudge      — Every N tasks, inject self-reflection prompt
2. Post-Use Skill Fix  — After using a skill, evaluate & patch it
3. Skill Auto-Creation — After complex tasks (5+ tool calls), suggest skill creation
4. Session Search      — FTS5-like search over past conversations
5. Frozen Memory       — Snapshot memory at session start, cache-aware
"""
import json
import os
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SESSION_DB_PATH = os.path.join(DATA_DIR, "sessions.db")
NUDGE_STATE_PATH = os.path.join(DATA_DIR, "nudge_state.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NUDGE_INTERVAL = 15         # Trigger periodic nudge every N tasks
COMPLEX_TASK_THRESHOLD = 5  # Tool calls before suggesting skill creation
SKILL_MAX_CHARS = 15000     # SKILL.md size limit
DESCRIPTION_BUDGET = 1024   # Max chars for skill descriptions


# ---------------------------------------------------------------------------
# 1. Periodic Nudge System
# ---------------------------------------------------------------------------
class NudgeManager:
    """Every N tasks, injects a self-reflection nudge into the agent's context."""

    NUDGE_PROMPT = """
## 🔄 Periodic Self-Reflection (auto-triggered)
You have completed {task_count} tasks since your last self-reflection. Take a moment to:
1. **Evaluate performance** — Which tasks went well? Which had errors or dead ends?
2. **Persist knowledge** — Is there anything you learned that should be saved to memory?
   - Facts about the user/project → use `remember` tool
   - Reusable workflows → consider creating a skill
3. **Improve skills** — Did any existing skills have missing steps, outdated commands, or unclear instructions?
   If so, update them now.
4. **Clean up** — Remove any memories that are no longer accurate.

This is your periodic nudge. You don't need to respond to it explicitly — just act on what's useful.
"""

    def __init__(self):
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(NUDGE_STATE_PATH):
            try:
                return json.loads(Path(NUDGE_STATE_PATH).read_text())
            except Exception as e:
                logger.debug("Suppressed error in learning_loop: %s", e)
        return {"task_count": 0, "last_nudge": None, "total_nudges": 0}

    def _save_state(self):
        Path(NUDGE_STATE_PATH).write_text(json.dumps(self._state, ensure_ascii=False, indent=2))

    def tick(self) -> Optional[str]:
        """Call after each task completion. Returns nudge prompt if triggered."""
        self._state["task_count"] = self._state.get("task_count", 0) + 1
        self._save_state()

        if self._state["task_count"] >= NUDGE_INTERVAL:
            nudge = self.NUDGE_PROMPT.format(task_count=self._state["task_count"])
            self._state["task_count"] = 0
            self._state["last_nudge"] = datetime.now().isoformat()
            self._state["total_nudges"] = self._state.get("total_nudges", 0) + 1
            self._save_state()
            return nudge.strip()
        return None

    def get_state(self) -> dict:
        return self._state.copy()


# ---------------------------------------------------------------------------
# 2. Post-Use Skill Improvement
# ---------------------------------------------------------------------------
SKILL_IMPROVEMENT_HINT = """After USING a skill, evaluate: did it have missing steps, outdated \
commands, unclear instructions, or repeated boilerplate you wrote by hand? \
If so, patch the skill immediately with the improvements — \
skills should get better every time they're used."""

SKILL_CREATION_HINT = """You just completed a complex task using {tool_count} tool calls. \
Consider saving this approach as a reusable skill with `create_skill` \
so you (or future sessions) can reuse it next time.

Good skills include:
- **Trigger conditions** — when to use this skill
- **Numbered steps** with exact commands
- **Pitfalls section** — known failure modes and fixes
- **Verification steps** — how to confirm it worked
- Use imperative commands but explain WHY behind each instruction
- Don't overfit to one scenario — write for the general case
- Keep under 500 lines; put large references separately"""


def should_suggest_skill_creation(tool_calls: list[str]) -> bool:
    """Returns True if the task was complex enough to suggest skill creation."""
    return len(tool_calls) >= COMPLEX_TASK_THRESHOLD


def get_skill_creation_hint(tool_calls: list[str]) -> str:
    """Build skill creation suggestion after a complex task."""
    return SKILL_CREATION_HINT.format(tool_count=len(tool_calls))


def get_skill_improvement_hint() -> str:
    """Hint injected when the agent uses an existing skill."""
    return SKILL_IMPROVEMENT_HINT


# ---------------------------------------------------------------------------
# 3. Skill Writing Principles (injected into system prompt)
# ---------------------------------------------------------------------------
SKILL_WRITING_PRINCIPLES = """
## Skill Management Principles
- After completing a complex task (5+ tool calls), fixing a tricky error, \
or discovering a non-trivial workflow, save the approach as a skill with `create_skill`.
- After USING a skill, evaluate and improve it: add missing steps, update outdated info, \
remove unnecessary parts. Skills improve through use.
- Skill descriptions should be "pushy" — explicitly list trigger conditions, edge cases, \
and synonyms so the skill gets loaded when relevant.
- Description budget: up to {desc_budget} characters. Be thorough, not terse.
- Good skills: trigger conditions, numbered steps with exact commands, pitfalls section, \
verification steps. Use imperative commands but explain WHY.
- Keep skills under 500 lines. Put large references in separate files.
- Don't overfit to one scenario — write for the general case.
- If you keep generating the same helper code, move it into the skill.
""".format(desc_budget=DESCRIPTION_BUDGET)


# ---------------------------------------------------------------------------
# 4. Session Search (FTS5 full-text search over past conversations)
# ---------------------------------------------------------------------------
class SessionSearchDB:
    """SQLite + FTS5 session storage for cross-session recall."""

    def __init__(self, db_path: str = SESSION_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    skill_used TEXT DEFAULT NULL,
                    tool_calls TEXT DEFAULT NULL
                )
            """)
            # FTS5 virtual table for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts
                USING fts5(content, thread_id, role, timestamp)
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Session DB init failed: {e}")

    def store(self, thread_id: str, role: str, content: str,
              skill_used: str | None = None, tool_calls: list[str] | None = None):
        """Store a message in the session database."""
        try:
            ts = datetime.now().isoformat()
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO sessions (thread_id, role, content, timestamp, skill_used, tool_calls) VALUES (?, ?, ?, ?, ?, ?)",
                (thread_id, role, content[:10000], ts, skill_used, json.dumps(tool_calls or [])),
            )
            conn.execute(
                "INSERT INTO sessions_fts (content, thread_id, role, timestamp) VALUES (?, ?, ?, ?)",
                (content[:10000], thread_id, role, ts),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Session store failed: {e}")

    def search(self, query: str, limit: int = 20, thread_id: str | None = None) -> list[dict]:
        """Full-text search over past conversations.

        Args:
            query: FTS5 query string (plain text, will be tokenized).
            limit: Max results to return.
            thread_id: If provided, restrict results to this thread.

        Returns dicts with content, thread_id, role, timestamp, rank, AND snippet
        (with <mark>...</mark> highlighting of query terms).
        """
        try:
            conn = sqlite3.connect(self.db_path)
            tokens = [t.strip().replace('"', '""') for t in query.split() if t.strip()]
            if not tokens:
                return []
            fts_query = " OR ".join(f'"{t}"' for t in tokens)
            sql = """SELECT content, thread_id, role, timestamp, rank,
                            snippet(sessions_fts, 0, '<mark>', '</mark>', '...', 32) as snip
                       FROM sessions_fts
                      WHERE sessions_fts MATCH ?"""
            params: list = [fts_query]
            if thread_id:
                sql += " AND thread_id = ?"
                params.append(thread_id)
            sql += " ORDER BY rank LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            return [
                {
                    "content": r[0][:500],
                    "thread_id": r[1],
                    "role": r[2],
                    "timestamp": r[3],
                    "rank": r[4],
                    "snippet": r[5],
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"Session search failed: {e}")
            return []

    def rebuild_from_threads(self, threads: list) -> int:
        """Rebuild FTS index from JSON ThreadStore (migration helper)."""
        count = 0
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM sessions_fts")
            conn.commit()
            for t in threads:
                tid = getattr(t, "id", None) or t.get("id")
                messages = getattr(t, "messages", None) or t.get("messages", [])
                for m in messages:
                    role = getattr(m, "role", None) or m.get("role", "user")
                    content = getattr(m, "content", None) or m.get("content", "")
                    ts_val = getattr(m, "created_at", None) or m.get("created_at")
                    ts = str(ts_val) if ts_val else datetime.now().isoformat()
                    if content:
                        conn.execute(
                            "INSERT INTO sessions (thread_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                            (tid, role, content[:10000], ts),
                        )
                        conn.execute(
                            "INSERT INTO sessions_fts (content, thread_id, role, timestamp) VALUES (?, ?, ?, ?)",
                            (content[:10000], tid, role, ts),
                        )
                        count += 1
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Rebuild from threads failed: {e}")
        return count

    def get_thread_history(self, thread_id: str, limit: int = 50) -> list[dict]:
        """Get full conversation history for a thread."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT role, content, timestamp, skill_used, tool_calls FROM sessions WHERE thread_id = ? ORDER BY id DESC LIMIT ?",
                (thread_id, limit),
            ).fetchall()
            conn.close()
            return [
                {"role": r[0], "content": r[1][:500], "timestamp": r[2], "skill_used": r[3], "tool_calls": json.loads(r[4] or "[]")}
                for r in reversed(rows)
            ]
        except Exception as e:
            logger.debug("Suppressed error in learning_loop: %s", e)
            return []

    def search_with_summary(self, query: str, limit: int = 5) -> dict:
        """Search sessions and produce a concise summary of results (Hermes pattern).
        Uses LLM if available, otherwise returns a rule-based summary."""
        results = self.search(query, limit=limit)
        if not results:
            return {"query": query, "results": [], "summary": "No matching sessions found."}

        # Build a text-based summary
        summary_parts = []
        for r in results:
            snippet = r["content"][:200].replace("\n", " ")
            summary_parts.append(f"- [{r['timestamp'][:10]}] ({r['role']}): {snippet}")
        summary = f"Found {len(results)} matching sessions for '{query}':\n" + "\n".join(summary_parts)

        # Try LLM summarization
        try:
            from app.models.provider import llm_provider
            model = llm_provider.get_chat_model(streaming=False)
            from langchain_core.messages import HumanMessage
            import asyncio
            prompt = f"Summarize these session search results in 2-3 sentences:\n\n{summary}"
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(model.ainvoke([HumanMessage(content=prompt)]))
            summary = result.content if hasattr(result, "content") else str(result)
        except Exception as e:
            logger.debug("Suppressed error in learning_loop: %s", e)
            pass  # Use rule-based summary

        return {"query": query, "results": results, "summary": summary}

    def get_stats(self) -> dict:
        try:
            conn = sqlite3.connect(self.db_path)
            total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            threads = conn.execute("SELECT COUNT(DISTINCT thread_id) FROM sessions").fetchone()[0]
            conn.close()
            return {"total_messages": total, "total_threads": threads}
        except Exception as e:
            logger.debug("Suppressed error in learning_loop: %s", e)
            return {"total_messages": 0, "total_threads": 0}


# ---------------------------------------------------------------------------
# 5. Frozen Memory Snapshot (cache-aware)
# ---------------------------------------------------------------------------
class FrozenMemorySnapshot:
    """Captures memory state once at session start. Changes during the session
    persist to disk but don't update the system prompt until next session.
    This preserves the LLM's prefix cache for performance."""

    def __init__(self):
        self._snapshots: dict[str, str] = {}  # thread_id -> frozen content

    def capture(self, thread_id: str, memory_content: str) -> str:
        """Capture and return frozen snapshot. Subsequent calls for the same
        thread_id return the original snapshot (immutable within session)."""
        if thread_id not in self._snapshots:
            self._snapshots[thread_id] = memory_content
        return self._snapshots[thread_id]

    def invalidate(self, thread_id: str):
        """Clear snapshot when a session ends (so next session gets fresh data)."""
        self._snapshots.pop(thread_id, None)

    def is_frozen(self, thread_id: str) -> bool:
        return thread_id in self._snapshots


# ---------------------------------------------------------------------------
# 6. Learnings Loop (Claude Code pattern: feedback → skill auto-update)
# ---------------------------------------------------------------------------
LEARNINGS_DIR = os.path.join(DATA_DIR, "learnings")
os.makedirs(LEARNINGS_DIR, exist_ok=True)


class LearningsLoop:
    """Implements the feedback→skill self-update closed loop.

    When a user corrects the agent after a skill is used, the correction
    is written back to a per-skill learnings file. On next invocation the
    learnings are loaded into the skill context so corrections persist.
    """

    def record_feedback(self, skill_name: str, feedback: str, context: str = "") -> dict:
        """Record user feedback/correction for a skill."""
        path = os.path.join(LEARNINGS_DIR, f"{skill_name}.md")
        entry = f"\n**{datetime.now().strftime('%Y-%m-%d')}** — {feedback.strip()}\n"
        if context:
            entry += f"  Context: {context.strip()[:300]}\n"

        existing = ""
        if os.path.exists(path):
            existing = Path(path).read_text(encoding="utf-8")

        # Duplicate check
        if feedback.strip() in existing:
            return {"recorded": False, "reason": "duplicate"}

        # Size cap: 5KB per skill
        if len(existing.encode("utf-8")) + len(entry.encode("utf-8")) > 5120:
            lines = existing.split("\n")
            # Remove oldest entries (top of file) to make room
            while len("\n".join(lines).encode("utf-8")) > 3072 and lines:
                lines.pop(0)
            existing = "\n".join(lines)

        content = existing + entry
        Path(path).write_text(content, encoding="utf-8")
        logger.info(f"Learnings recorded for skill '{skill_name}': {feedback[:80]}")
        return {"recorded": True, "skill": skill_name, "total_bytes": len(content.encode("utf-8"))}

    def get_learnings(self, skill_name: str) -> str:
        """Get accumulated learnings for a skill."""
        path = os.path.join(LEARNINGS_DIR, f"{skill_name}.md")
        if not os.path.exists(path):
            return ""
        return Path(path).read_text(encoding="utf-8")

    def get_skill_context(self, skill_name: str) -> str:
        """Build enhanced skill context with learnings injected."""
        learnings = self.get_learnings(skill_name)
        if not learnings:
            return ""
        return f"\n## Learnings (auto-accumulated from feedback)\n{learnings}"

    def list_all_learnings(self) -> list[dict]:
        """List all skills that have learnings."""
        results = []
        for f in Path(LEARNINGS_DIR).glob("*.md"):
            content = f.read_text(encoding="utf-8")
            entries = [l for l in content.split("\n") if l.strip().startswith("**")]
            results.append({
                "skill": f.stem,
                "entries": len(entries),
                "bytes": len(content.encode("utf-8")),
            })
        return results

    def apply_feedback_to_skill(self, skill_name: str) -> tuple[bool, str]:
        """Auto-apply accumulated learnings as a skill patch (instruction-level update)."""
        learnings = self.get_learnings(skill_name)
        if not learnings:
            return False, "No learnings to apply"

        try:
            from app.agents.evolution import skill_registry
            skill = skill_registry.get_skill(skill_name)
            if not skill:
                return False, f"Skill '{skill_name}' not found"

            # Append learnings section to system_prompt if not already present
            prompt = skill.get("system_prompt", "")
            if "## Learnings" in prompt:
                # Update existing learnings section
                before = prompt.split("## Learnings")[0]
                new_prompt = before + "## Learnings (auto-accumulated from feedback)\n" + learnings
            else:
                new_prompt = prompt + "\n\n## Learnings (auto-accumulated from feedback)\n" + learnings

            ok, msg = skill_registry.edit_skill(skill_name, new_prompt)
            return ok, msg
        except Exception as e:
            return False, str(e)


# ---------------------------------------------------------------------------
# 7. Memory Formatting with § separators + capacity percentage
# ---------------------------------------------------------------------------
def format_memory_for_prompt(entries: list[str], target: str = "memory",
                              max_chars: int = 2200) -> str:
    """Format memory entries for system prompt injection (Hermes pattern).
    Uses § separators and shows capacity percentage."""
    if not entries:
        return ""

    content = "§".join(e.strip() for e in entries if e.strip())
    used = len(content)
    pct = min(100, round(used / max_chars * 100))
    header_name = "MEMORY (your personal notes)" if target == "memory" else "USER PROFILE"

    header = (
        f"══════════════════════════════════════════════\n"
        f"{header_name} [{pct}% — {used:,}/{max_chars:,} chars]\n"
        f"══════════════════════════════════════════════"
    )
    return f"{header}\n{content}"


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
nudge_manager = NudgeManager()
session_search_db = SessionSearchDB()
frozen_memory = FrozenMemorySnapshot()
learnings_loop = LearningsLoop()
