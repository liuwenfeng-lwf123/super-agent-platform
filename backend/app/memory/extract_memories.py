"""
Session Memory Extraction — inspired by Claude Code's extractMemories service.

Automatically extracts durable memories from conversation transcripts at the
end of each query loop. Uses a 4-type taxonomy:
  - user: role, preferences, knowledge
  - feedback: corrections and confirmed approaches
  - project: ongoing work, goals, deadlines
  - reference: pointers to external systems

Also implements Auto-Dream: periodic cross-session memory consolidation.
"""
import json
import logging
import os
import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.agents.cost_tracker import estimate_tokens
from app.models.provider import llm_provider

logger = logging.getLogger(__name__)

# --- Configuration ---
MIN_MESSAGES_TO_EXTRACT = 2         # Don't extract from empty conversations
MIN_TOKENS_BETWEEN_EXTRACTIONS = 300  # Minimum context growth between extractions
EXTRACTION_COOLDOWN_SECONDS = 60    # Min seconds between extractions
DREAM_MIN_HOURS = 24                # Min hours between consolidations
DREAM_MIN_SESSIONS = 5              # Min sessions since last consolidation

MEMORY_TYPES = ["user", "feedback", "project", "reference"]

EXTRACT_PROMPT = """You are the memory extraction subagent. Analyze the conversation above and extract durable memories worth saving.

## Types of memory

<types>
<type>
    <name>user</name>
    <description>Information about the user's role, goals, preferences, and knowledge. Helps tailor future responses.</description>
    <when_to_save>When you learn details about the user's role, preferences, or expertise.</when_to_save>
</type>
<type>
    <name>feedback</name>
    <description>Guidance the user gave about how to approach work — corrections AND confirmed approaches.</description>
    <when_to_save>When the user corrects your approach or confirms a non-obvious approach worked.</when_to_save>
</type>
<type>
    <name>project</name>
    <description>Ongoing work, goals, deadlines, decisions — not derivable from code/git.</description>
    <when_to_save>When you learn who is doing what, why, or by when.</when_to_save>
</type>
<type>
    <name>reference</name>
    <description>Pointers to external resources, tools, dashboards, docs.</description>
    <when_to_save>When the user mentions external systems and their purpose.</when_to_save>
</type>
</types>

## What NOT to save
- Code patterns, architecture, file paths — derivable from reading the project
- Git history or recent changes — use git log
- Ephemeral task details or temporary state
- Anything already in project memory (MEMORY.md)

## Output format
Return a JSON array of memories to save. Each entry:
{"type": "user|feedback|project|reference", "key": "short_title", "value": "concise memory content"}

If nothing worth saving, return an empty array: []

IMPORTANT: Only extract what is genuinely durable and non-obvious. Quality over quantity."""

DREAM_PROMPT = """You are the memory consolidation agent. Review the accumulated memories below and:

1. **Merge** duplicates or near-duplicates into single entries
2. **Remove** memories that are now stale or contradicted by newer ones
3. **Promote** important patterns you notice across multiple memories
4. **Trim** memories that are too verbose — keep them concise

Return a JSON object:
{
  "keep": [{"type": "...", "key": "...", "value": "..."}],
  "remove_keys": ["key1", "key2"],
  "summary": "one-line description of what changed"
}"""


class MemoryExtractor:
    """Extracts durable memories from conversation transcripts."""

    def __init__(self):
        self._session_state: dict[str, dict[str, object]] = {}
        self._extraction_count: int = 0

    def _get_session_key(self, session_id: str) -> str:
        return session_id or "__global__"

    def _get_or_create_session_state(self, session_id: str) -> dict[str, object]:
        key = self._get_session_key(session_id)
        if key not in self._session_state:
            self._session_state[key] = {
                "last_extraction_at": None,
                "tokens_at_last_extraction": 0,
            }
        return self._session_state[key]

    async def maybe_extract(
        self,
        messages: list[dict],
        session_id: str = "",
        force: bool = False,
    ) -> list[dict]:
        """Extract memories if conditions are met. Returns list of extracted memories."""
        from app.config import settings
        if not settings.enable_memory_extraction and not force:
            return []
        session_state = self._get_or_create_session_state(session_id)
        if not force:
            # Check message count
            if len(messages) < MIN_MESSAGES_TO_EXTRACT:
                return []

            # Check cooldown
            last_extraction_at = session_state.get("last_extraction_at")
            if isinstance(last_extraction_at, datetime):
                elapsed = (datetime.now() - last_extraction_at).total_seconds()
                if elapsed < EXTRACTION_COOLDOWN_SECONDS:
                    return []

            # Check token growth
            total_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
            tokens_at_last_extraction = int(session_state.get("tokens_at_last_extraction", 0))
            if total_tokens - tokens_at_last_extraction < MIN_TOKENS_BETWEEN_EXTRACTIONS:
                return []

        # Build conversation transcript for extraction
        transcript = self._build_transcript(messages)
        if not transcript:
            return []

        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            chat_model = llm_provider.get_chat_model(streaming=False)
            response = await chat_model.ainvoke([
                SystemMessage(content=EXTRACT_PROMPT),
                HumanMessage(content=f"Conversation to analyze:\n\n{transcript}"),
            ])

            content = response.content if hasattr(response, "content") else str(response)
            memories = self._parse_memories(content)

            session_state["last_extraction_at"] = datetime.now()
            session_state["tokens_at_last_extraction"] = sum(
                estimate_tokens(m.get("content", "")) for m in messages
            )
            self._extraction_count += 1

            if memories:
                logger.info(f"Extracted {len(memories)} memories from session {session_id}")
                await self._persist_memories(memories, session_id)

            return memories

        except Exception as e:
            logger.warning(f"Memory extraction failed: {e}")
            return []

    def _build_transcript(self, messages: list[dict], max_chars: int = 15000) -> str:
        """Build a condensed transcript from messages."""
        lines = []
        total_chars = 0
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if not content:
                continue
            # Truncate individual messages
            if len(content) > 500:
                content = content[:500] + "..."
            line = f"[{role}]: {content}"
            if total_chars + len(line) > max_chars:
                break
            lines.append(line)
            total_chars += len(line)
        return "\n\n".join(lines)

    def _parse_memories(self, content: str) -> list[dict]:
        """Parse LLM output into memory entries."""
        # Try to find JSON array in the response
        content = content.strip()

        # Handle markdown code blocks
        if "```" in content:
            import re
            match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
            if match:
                content = match.group(1).strip()

        try:
            data = json.loads(content)
            if isinstance(data, list):
                # Validate each entry
                valid = []
                for item in data:
                    if (isinstance(item, dict) and
                        item.get("type") in MEMORY_TYPES and
                        item.get("key") and
                        item.get("value")):
                        valid.append({
                            "type": item["type"],
                            "key": item["key"],
                            "value": item["value"],
                            "extracted_at": datetime.now().isoformat(),
                        })
                return valid
        except json.JSONDecodeError:
            pass

        return []

    async def _persist_memories(self, memories: list[dict], session_id: str):
        """Save extracted memories to the layered store."""
        try:
            from app.memory.layered_store import layered_memory

            for mem in memories:
                mem_type = mem["type"]
                key = mem["key"]
                value = mem["value"]

                if mem_type == "user":
                    layered_memory.set_user_memory(key, value, category="extracted")
                elif mem_type in ("feedback", "project", "reference"):
                    layered_memory.add_agent_memory(
                        f"_extracted_{mem_type}",
                        key, value, category=mem_type
                    )

                # Also add to session memory
                if session_id:
                    layered_memory.add_session_memory(
                        session_id,
                        f"[{mem_type}] {key}: {value}",
                        category="extracted"
                    )

        except Exception as e:
            logger.warning(f"Failed to persist memories: {e}")

    def get_stats(self) -> dict:
        return {
            "extraction_count": self._extraction_count,
            "active_sessions": len(self._session_state),
            "last_extraction_at": max(
                (
                    state["last_extraction_at"].isoformat()
                    for state in self._session_state.values()
                    if isinstance(state.get("last_extraction_at"), datetime)
                ),
                default=None,
            ),
        }


class AutoDream:
    """Cross-session memory consolidation — merges, deduplicates, and promotes memories.

    Inspired by Claude Code's autoDream service. Runs periodically when:
    1. Time gate: >= DREAM_MIN_HOURS since last consolidation
    2. Session gate: >= DREAM_MIN_SESSIONS new sessions since last consolidation
    """

    def __init__(self):
        self._state_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "dream_state.json"
        )
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self._state_path):
            try:
                return json.loads(Path(self._state_path).read_text(encoding="utf-8"))
            except Exception as e:
                logger.debug("Suppressed error in extract_memories: %s", e)
        return {
            "last_consolidated_at": None,
            "sessions_since_last": 0,
            "total_consolidations": 0,
        }

    def _save_state(self):
        try:
            dir_name = os.path.dirname(self._state_path)
            os.makedirs(dir_name, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._state, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._state_path)
            except Exception as e:
                logger.debug("Suppressed error in extract_memories: %s", e)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.debug("Suppressed error in extract_memories: %s", e)

    def record_session(self):
        """Called at the end of each session to increment counter."""
        self._state["sessions_since_last"] = self._state.get("sessions_since_last", 0) + 1
        self._save_state()

    def should_consolidate(self) -> bool:
        """Check if consolidation should run."""
        # Time gate
        last = self._state.get("last_consolidated_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                hours_elapsed = (datetime.now() - last_dt).total_seconds() / 3600
                if hours_elapsed < DREAM_MIN_HOURS:
                    return False
            except (ValueError, TypeError):
                pass

        # Session gate
        sessions = self._state.get("sessions_since_last", 0)
        return sessions >= DREAM_MIN_SESSIONS

    async def consolidate(self) -> dict:
        """Run memory consolidation."""
        from app.memory.layered_store import layered_memory

        # Gather all agent memories
        all_memories = []
        stats = layered_memory.get_stats()
        for agent_type in stats.get("agent_types", []):
            mems = layered_memory.get_agent_memory(agent_type)
            for m in mems:
                m["_source"] = agent_type
            all_memories.extend(mems)

        # Also include user memory
        user_mem = layered_memory.get_user_memory()
        for category, entries in user_mem.items():
            if isinstance(entries, dict):
                for k, v in entries.items():
                    all_memories.append({"key": k, "value": v, "category": category, "_source": "user"})

        if len(all_memories) < 3:
            return {"skipped": True, "reason": "Not enough memories to consolidate"}

        # Build memory dump for LLM
        memory_text = json.dumps(all_memories, ensure_ascii=False, indent=2)

        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            chat_model = llm_provider.get_chat_model(streaming=False)
            response = await chat_model.ainvoke([
                SystemMessage(content=DREAM_PROMPT),
                HumanMessage(content=f"Memories to consolidate ({len(all_memories)} total):\n\n{memory_text}"),
            ])

            content = response.content if hasattr(response, "content") else str(response)

            # Parse response
            if "```" in content:
                import re
                match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
                if match:
                    content = match.group(1).strip()

            result = json.loads(content)

            # Apply removals
            removed = 0
            for key in result.get("remove_keys", []):
                for agent_type in stats.get("agent_types", []):
                    mems = layered_memory.get_agent_memory(agent_type)
                    new_mems = [m for m in mems if m.get("key") != key]
                    if len(new_mems) < len(mems):
                        removed += len(mems) - len(new_mems)
                        layered_memory._save_agent_memory(agent_type, new_mems)

            # Update state
            self._state["last_consolidated_at"] = datetime.now().isoformat()
            self._state["sessions_since_last"] = 0
            self._state["total_consolidations"] = self._state.get("total_consolidations", 0) + 1
            self._save_state()

            return {
                "consolidated": True,
                "kept": len(result.get("keep", [])),
                "removed": removed,
                "summary": result.get("summary", ""),
                "total_consolidations": self._state["total_consolidations"],
            }

        except Exception as e:
            logger.warning(f"Memory consolidation failed: {e}")
            return {"error": str(e)}

    def get_state(self) -> dict:
        return {
            **self._state,
            "should_consolidate": self.should_consolidate(),
        }


# Singletons
memory_extractor = MemoryExtractor()
auto_dream = AutoDream()
