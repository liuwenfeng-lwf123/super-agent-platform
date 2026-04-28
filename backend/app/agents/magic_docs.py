from __future__ import annotations
import logging

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.agents.tool_runtime import is_policy_allowed


logger = logging.getLogger(__name__)
MAGIC_DOC_HEADER_PATTERN = re.compile(r"^#\s*MAGIC\s+DOC:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
MAGIC_DOCS_PATH = Path(settings.data_dir) / "magic_docs.json"


class MagicDocsRegistry:
    def __init__(self):
        self._docs: dict[str, dict[str, Any]] = {}
        self._auto_sync_tasks: dict[str, asyncio.Task] = {}
        self._load()

    def _load(self):
        try:
            if MAGIC_DOCS_PATH.exists():
                data = json.loads(MAGIC_DOCS_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._docs = data
        except Exception as e:
            logger.debug("Suppressed error in magic_docs: %s", e)
            self._docs = {}

    def _save(self):
        MAGIC_DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
        MAGIC_DOCS_PATH.write_text(
            json.dumps(self._docs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def detect_magic_doc_header(self, content: str) -> dict[str, str] | None:
        match = MAGIC_DOC_HEADER_PATTERN.search(content or "")
        if not match:
            return None
        return {"title": match.group(1).strip()}

    def register(self, file_path: str, content: str | None = None) -> dict[str, Any] | None:
        if not settings.enable_magic_docs:
            return None
        if not is_policy_allowed("feature.magic_docs", True):
            return None
        target = Path(file_path)
        if content is None:
            if not target.exists() or not target.is_file():
                return None
            content = target.read_text(encoding="utf-8")
        detected = self.detect_magic_doc_header(content)
        if not detected:
            return None
        key = str(target)
        current = self._docs.get(key, {})
        current.update(
            {
                "path": key,
                "title": detected["title"],
                "updated_at": datetime.now().isoformat(),
                "pending_notes": current.get("pending_notes", []),
                "auto_sync": current.get("auto_sync", True),
            }
        )
        self._docs[key] = current
        self._save()
        return current

    def list_docs(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._docs.values()]

    async def _debounced_sync(self, file_path: str, delay_seconds: float = 12.0):
        try:
            await asyncio.sleep(delay_seconds)
            self.sync_doc(file_path, trigger="auto")
        except asyncio.CancelledError:
            raise
        finally:
            self._auto_sync_tasks.pop(file_path, None)

    def _schedule_auto_sync(self, file_path: str):
        item = self._docs.get(file_path)
        if not item:
            return
        if not bool(item.get("auto_sync", True)):
            return
        existing = self._auto_sync_tasks.get(file_path)
        if existing and not existing.done():
            existing.cancel()
        try:
            self._auto_sync_tasks[file_path] = asyncio.create_task(self._debounced_sync(file_path))
        except RuntimeError:
            return

    def record_session(self, thread_id: str, user_message: str, assistant_content: str, tool_summary: str | None = None):
        if not self._docs:
            return
        if not settings.enable_magic_docs or not is_policy_allowed("feature.magic_docs", True):
            return
        note = {
            "thread_id": thread_id,
            "created_at": datetime.now().isoformat(),
            "user": user_message[:500],
            "assistant": assistant_content[:1000],
            "tool_summary": tool_summary or "",
        }
        for item in self._docs.values():
            pending = item.setdefault("pending_notes", [])
            pending.append(note)
            if len(pending) > 20:
                item["pending_notes"] = pending[-10:]
        self._save()
        for file_path in list(self._docs.keys()):
            self._schedule_auto_sync(file_path)

    def sync_doc(self, file_path: str, trigger: str = "manual") -> dict[str, Any]:
        key = str(Path(file_path))
        item = self._docs.get(key)
        if not item:
            return {"error": "Magic doc not registered"}
        pending = item.get("pending_notes", [])
        if not pending:
            return {"status": "noop", "path": key, "updated": False}
        target = Path(key)
        if not target.exists() or not target.is_file():
            return {"error": "Magic doc file not found"}
        content = target.read_text(encoding="utf-8")
        section_lines = ["", "## Auto-updates"]
        for note in pending[-5:]:
            detail = note.get("tool_summary") or note.get("assistant") or note.get("user")
            section_lines.append(f"- {note['created_at']}: {detail[:300]}")
        if "## Auto-updates" in content:
            content = re.sub(
                r"\n## Auto-updates[\s\S]*$",
                "\n" + "\n".join(section_lines[1:]),
                content,
                count=1,
            )
        else:
            content = content.rstrip() + "\n" + "\n".join(section_lines) + "\n"
        target.write_text(content, encoding="utf-8")
        item["pending_notes"] = []
        item["updated_at"] = datetime.now().isoformat()
        item["last_sync_trigger"] = trigger
        self._save()
        return {"status": "updated", "path": key, "updated": True, "trigger": trigger}

    def set_auto_sync(self, file_path: str, enabled: bool) -> dict[str, Any]:
        key = str(Path(file_path))
        item = self._docs.get(key)
        if not item:
            return {"error": "Magic doc not registered"}
        item["auto_sync"] = bool(enabled)
        self._save()
        if not enabled:
            task = self._auto_sync_tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
        return {"path": key, "auto_sync": item["auto_sync"]}


magic_docs = MagicDocsRegistry()
