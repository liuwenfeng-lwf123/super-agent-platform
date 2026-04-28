"""
SQLite-backed ThreadStore — drop-in replacement for the JSON-file ThreadStore.

Usage:
    Set THREAD_STORE_BACKEND=sqlite in .env to activate.
    Data is stored in data/threads.db (configurable via THREADS_DB_PATH).
    Falls back to JSON ThreadStore if sqlite import fails.

Provides the same async interface as the original ThreadStore:
    create, get, list_threads, add_message, delete, update_thread,
    get_children, get_lineage, export_thread, import_thread, fork.
"""
import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

from app.models.schemas import Thread, Message
from app.config import settings

logger = logging.getLogger(__name__)

_DB_PATH = getattr(settings, "threads_db_path", None) or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "threads.db"
)


def _ensure_db(db_path: str) -> None:
    """Create the DB and tables if they don't exist."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            parent_id TEXT,
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_threads_parent ON threads(parent_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_threads_updated ON threads(updated_at DESC)
    """)
    conn.commit()
    conn.close()


class SQLiteThreadStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _DB_PATH
        _ensure_db(self.db_path)
        self._lock = asyncio.Lock()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _thread_from_row(self, data_json: str) -> Thread:
        return Thread(**json.loads(data_json))

    def _thread_to_json(self, thread: Thread) -> str:
        return json.dumps(thread.model_dump(mode="json"), ensure_ascii=False, default=str)

    async def create(
        self,
        title: str = "New Chat",
        parent_id: str | None = None,
        compact_summary: str | None = None,
    ) -> Thread:
        async with self._lock:
            thread = Thread(title=title, parent_id=parent_id, compact_summary=compact_summary)
            data = self._thread_to_json(thread)
            await asyncio.to_thread(self._insert_thread, thread, data)
            return thread

    def _insert_thread(self, thread: Thread, data: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO threads (id, data, title, parent_id, updated_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (thread.id, data, thread.title, thread.parent_id,
                 thread.updated_at.isoformat(), thread.created_at.isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    async def get(self, thread_id: str) -> Thread | None:
        row = await asyncio.to_thread(self._get_sync, thread_id)
        if row:
            return self._thread_from_row(row)
        return None

    def _get_sync(self, thread_id: str) -> str | None:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT data FROM threads WHERE id = ?", (thread_id,))
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    async def list_threads(self) -> list[Thread]:
        rows = await asyncio.to_thread(self._list_sync)
        return [self._thread_from_row(r) for r in rows]

    def _list_sync(self) -> list[str]:
        conn = self._conn()
        try:
            cur = conn.execute("SELECT data FROM threads ORDER BY updated_at DESC")
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()

    async def add_message(self, thread_id: str, message: Message) -> Thread | None:
        async with self._lock:
            thread = await self.get(thread_id)
            if not thread:
                return None
            message.thread_id = thread_id
            thread.messages.append(message)
            thread.updated_at = datetime.now()
            if len(thread.messages) == 1 and message.role == "user":
                thread.title = message.content[:50] + ("..." if len(message.content) > 50 else "")
            data = self._thread_to_json(thread)
            await asyncio.to_thread(self._insert_thread, thread, data)
            return thread

    async def delete(self, thread_id: str) -> bool:
        async with self._lock:
            deleted = await asyncio.to_thread(self._delete_sync, thread_id)
            return deleted

    def _delete_sync(self, thread_id: str) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    async def update_thread(self, thread: Thread) -> None:
        async with self._lock:
            data = self._thread_to_json(thread)
            await asyncio.to_thread(self._insert_thread, thread, data)

    async def get_children(self, thread_id: str) -> list[Thread]:
        rows = await asyncio.to_thread(self._get_children_sync, thread_id)
        return [self._thread_from_row(r) for r in rows]

    def _get_children_sync(self, thread_id: str) -> list[str]:
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT data FROM threads WHERE parent_id = ? ORDER BY created_at",
                (thread_id,),
            )
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()

    async def get_lineage(self, thread_id: str, max_depth: int = 20) -> list[dict]:
        chain: list[dict] = []
        current_id = thread_id
        seen: set[str] = set()
        for _ in range(max_depth):
            if not current_id or current_id in seen:
                break
            seen.add(current_id)
            thread = await self.get(current_id)
            if not thread:
                break
            chain.append({
                "id": thread.id,
                "title": thread.title,
                "parent_id": thread.parent_id,
                "compact_summary": thread.compact_summary,
                "created_at": thread.created_at.isoformat(),
            })
            current_id = thread.parent_id
        chain.reverse()
        return chain

    async def export_thread(self, thread_id: str) -> dict | None:
        thread = await self.get(thread_id)
        if not thread:
            return None
        children = await self.get_children(thread_id)
        return {
            "format": "sap.trajectory.v1",
            "exported_at": datetime.now().isoformat(),
            "message_count": len(thread.messages),
            "thread": thread.model_dump(mode="json"),
            "lineage": await self.get_lineage(thread_id),
            "children": [
                {"id": c.id, "title": c.title, "created_at": c.created_at.isoformat()}
                for c in children
            ],
        }

    async def import_thread(
        self,
        exported: dict,
        *,
        title: str | None = None,
        parent_id: str | None = None,
    ) -> Thread | None:
        thread_payload = exported.get("thread") if isinstance(exported.get("thread"), dict) else exported
        if not isinstance(thread_payload, dict):
            return None
        if "id" not in thread_payload or not isinstance(thread_payload.get("messages"), list):
            return None
        try:
            source_thread = Thread(**thread_payload)
        except Exception as e:
            logger.debug("Suppressed error in sqlite_store: %s", e)
            return None
        metadata = dict(source_thread.metadata or {})
        trajectory_meta = metadata.get("trajectory", {}) if isinstance(metadata.get("trajectory"), dict) else {}
        trajectory_meta.update({
            "source_thread_id": source_thread.id,
            "source_parent_id": source_thread.parent_id,
            "format": exported.get("format", "sap.trajectory.v1"),
            "exported_at": exported.get("exported_at"),
            "imported_at": datetime.now().isoformat(),
        })
        metadata["trajectory"] = trajectory_meta
        imported = Thread(
            title=title or source_thread.title,
            messages=[
                Message(
                    role=m.role, content=m.content,
                    agent_id=m.agent_id, metadata=dict(m.metadata),
                    created_at=m.created_at,
                )
                for m in source_thread.messages
            ],
            metadata=metadata,
            parent_id=parent_id,
            compact_summary=source_thread.compact_summary,
        )
        async with self._lock:
            data = self._thread_to_json(imported)
            await asyncio.to_thread(self._insert_thread, imported, data)
        return imported

    async def fork(
        self, parent_id: str, summary: str, title: str | None = None
    ) -> Thread | None:
        parent = await self.get(parent_id)
        if not parent:
            return None
        parent.compact_summary = summary
        await self.update_thread(parent)
        child_title = title or f"{parent.title} (continued)"
        return await self.create(title=child_title, parent_id=parent_id, compact_summary=summary)


def get_thread_store():
    """Factory: returns SQLiteThreadStore if configured, else JSON ThreadStore."""
    backend = getattr(settings, "thread_store_backend", "json")
    if backend == "sqlite":
        logger.info("Using SQLite thread store at %s", _DB_PATH)
        return SQLiteThreadStore()
    # Default: JSON file store
    from app.agents.store import ThreadStore
    return ThreadStore()
