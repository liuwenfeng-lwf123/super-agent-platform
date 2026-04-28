from app.models.schemas import Thread, Message
import logging
from app.config import settings
import asyncio
import json
import os
import tempfile
from datetime import datetime



logger = logging.getLogger(__name__)
class ThreadStore:
    def __init__(self, storage_path: str | None = None):
        self.storage_path = storage_path or settings.threads_dir
        self._threads: dict[str, Thread] = {}
        os.makedirs(self.storage_path, exist_ok=True)
        self._lock = asyncio.Lock()
        self._load()

    def _filepath(self, thread_id: str) -> str:
        return os.path.join(self.storage_path, f"{thread_id}.json")

    def _load(self):
        for filename in os.listdir(self.storage_path):
            if filename.endswith(".json"):
                try:
                    with open(
                        os.path.join(self.storage_path, filename), "r", encoding="utf-8"
                    ) as f:
                        data = json.load(f)
                    thread = Thread(**data)
                    self._threads[thread.id] = thread
                except (json.JSONDecodeError, Exception):
                    continue

    def _save_sync(self, filepath: str, data_bytes: bytes):
        """Write pre-serialised data to disk atomically (runs in thread)."""
        fd, tmp_path = tempfile.mkstemp(dir=self.storage_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data_bytes)
            os.replace(tmp_path, filepath)
        except Exception as e:
            logger.debug("Suppressed error in store: %s", e)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def _save_async(self, thread: Thread):
        """Serialize in-memory, then offload file I/O to a thread."""
        filepath = self._filepath(thread.id)
        data_bytes = json.dumps(
            thread.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str
        ).encode("utf-8")
        await asyncio.to_thread(self._save_sync, filepath, data_bytes)

    def _save(self, thread: Thread):
        """Synchronous fallback (used during __init__ / _load only)."""
        filepath = self._filepath(thread.id)
        fd, tmp_path = tempfile.mkstemp(dir=self.storage_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(thread.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp_path, filepath)
        except Exception as e:
            logger.debug("Suppressed error in store: %s", e)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def create(
        self,
        title: str = "New Chat",
        parent_id: str | None = None,
        compact_summary: str | None = None,
    ) -> Thread:
        async with self._lock:
            thread = Thread(
                title=title,
                parent_id=parent_id,
                compact_summary=compact_summary,
            )
            self._threads[thread.id] = thread
            await self._save_async(thread)
            return thread

    async def get(self, thread_id: str) -> Thread | None:
        return self._threads.get(thread_id)

    async def list_threads(self) -> list[Thread]:
        threads = list(self._threads.values())
        threads.sort(key=lambda t: t.updated_at, reverse=True)
        return threads

    async def add_message(self, thread_id: str, message: Message) -> Thread | None:
        async with self._lock:
            thread = self._threads.get(thread_id)
            if not thread:
                return None
            message.thread_id = thread_id
            thread.messages.append(message)
            thread.updated_at = datetime.now()
            if len(thread.messages) == 1 and message.role == "user":
                thread.title = message.content[:50] + ("..." if len(message.content) > 50 else "")
            await self._save_async(thread)
            return thread

    async def delete(self, thread_id: str) -> bool:
        async with self._lock:
            if thread_id in self._threads:
                del self._threads[thread_id]
                filepath = self._filepath(thread_id)
                if os.path.exists(filepath):
                    os.remove(filepath)
                return True
            return False


    async def update_thread(self, thread: Thread) -> None:
        """Thread-safe save of a thread object (use instead of _save directly)."""
        async with self._lock:
            self._threads[thread.id] = thread
            await self._save_async(thread)

    async def get_children(self, thread_id: str) -> list[Thread]:
        """Return all threads whose parent_id is thread_id."""
        return sorted(
            [t for t in self._threads.values() if t.parent_id == thread_id],
            key=lambda t: t.created_at,
        )

    async def get_lineage(self, thread_id: str, max_depth: int = 20) -> list[dict]:
        """Walk ancestor chain from thread_id up to root. Returns list of
        {id, title, parent_id, compact_summary, created_at} dicts, oldest first."""
        chain: list[dict] = []
        current_id = thread_id
        seen: set[str] = set()
        for _ in range(max_depth):
            if not current_id or current_id in seen:
                break
            seen.add(current_id)
            thread = self._threads.get(current_id)
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
        chain.reverse()  # oldest first
        return chain

    async def export_thread(self, thread_id: str) -> dict | None:
        thread = self._threads.get(thread_id)
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
                {
                    "id": child.id,
                    "title": child.title,
                    "created_at": child.created_at.isoformat(),
                }
                for child in children
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
            logger.debug("Suppressed error in store: %s", e)
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
                    role=message.role,
                    content=message.content,
                    agent_id=message.agent_id,
                    metadata=dict(message.metadata),
                    created_at=message.created_at,
                )
                for message in source_thread.messages
            ],
            metadata=metadata,
            parent_id=parent_id,
            compact_summary=source_thread.compact_summary,
        )
        async with self._lock:
            self._threads[imported.id] = imported
            await self._save_async(imported)
        return imported

    async def fork(
        self, parent_id: str, summary: str, title: str | None = None
    ) -> Thread | None:
        """Create a child session from a parent, carrying the compact summary.
        Also records the summary on the parent thread."""
        parent = self._threads.get(parent_id)
        if not parent:
            return None
        async with self._lock:
            parent.compact_summary = summary
            await self._save_async(parent)
        child_title = title or f"{parent.title} (continued)"
        return await self.create(
            title=child_title,
            parent_id=parent_id,
            compact_summary=summary,
        )


def _create_thread_store():
    """Factory: returns SQLiteThreadStore if configured, else JSON ThreadStore."""
    from app.config import settings as _s
    if getattr(_s, "thread_store_backend", "json") == "sqlite":
        from app.agents.sqlite_store import SQLiteThreadStore
        return SQLiteThreadStore()
    return ThreadStore()


thread_store = _create_thread_store()
