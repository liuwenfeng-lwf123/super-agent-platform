from app.models.schemas import Thread, Message
from app.config import settings
import json
import os
from datetime import datetime


class ThreadStore:
    def __init__(self, storage_path: str | None = None):
        self.storage_path = storage_path or settings.threads_dir
        self._threads: dict[str, Thread] = {}
        os.makedirs(self.storage_path, exist_ok=True)
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

    def _save(self, thread: Thread):
        filepath = self._filepath(thread.id)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(thread.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)

    async def create(self, title: str = "New Chat") -> Thread:
        thread = Thread(title=title)
        self._threads[thread.id] = thread
        self._save(thread)
        return thread

    async def get(self, thread_id: str) -> Thread | None:
        return self._threads.get(thread_id)

    async def list_threads(self) -> list[Thread]:
        threads = list(self._threads.values())
        threads.sort(key=lambda t: t.updated_at, reverse=True)
        return threads

    async def add_message(self, thread_id: str, message: Message) -> Thread | None:
        thread = self._threads.get(thread_id)
        if not thread:
            return None
        message.thread_id = thread_id
        thread.messages.append(message)
        thread.updated_at = datetime.now()
        if len(thread.messages) == 1 and message.role == "user":
            thread.title = message.content[:50] + ("..." if len(message.content) > 50 else "")
        self._save(thread)
        return thread

    async def delete(self, thread_id: str) -> bool:
        if thread_id in self._threads:
            del self._threads[thread_id]
            filepath = self._filepath(thread_id)
            if os.path.exists(filepath):
                os.remove(filepath)
            return True
        return False


thread_store = ThreadStore()
