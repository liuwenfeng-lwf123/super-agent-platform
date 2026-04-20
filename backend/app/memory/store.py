from app.models.schemas import MemoryEntry
from app.config import settings
import json
import os
from datetime import datetime


class MemoryStore:
    def __init__(self, storage_path: str | None = None):
        self.storage_path = storage_path or settings.memory_dir
        self._entries: dict[str, MemoryEntry] = {}
        os.makedirs(self.storage_path, exist_ok=True)
        self._load()

    def _filepath(self) -> str:
        return os.path.join(self.storage_path, "memory.json")

    def _load(self):
        filepath = self._filepath()
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    entry = MemoryEntry(**item)
                    self._entries[entry.id] = entry
            except (json.JSONDecodeError, Exception):
                self._entries = {}

    def _save(self):
        filepath = self._filepath()
        data = [entry.model_dump(mode="json") for entry in self._entries.values()]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    async def add(self, key: str, value: str, category: str = "knowledge") -> MemoryEntry:
        for entry in self._entries.values():
            if entry.key == key and entry.category == category:
                entry.value = value
                entry.updated_at = datetime.now()
                self._save()
                return entry

        entry = MemoryEntry(key=key, value=value, category=category)
        self._entries[entry.id] = entry
        self._save()
        return entry

    async def get_all(self) -> list[MemoryEntry]:
        return list(self._entries.values())

    async def search(self, query: str) -> list[MemoryEntry]:
        query_lower = query.lower()
        return [
            e
            for e in self._entries.values()
            if query_lower in e.key.lower() or query_lower in e.value.lower()
        ]

    async def delete(self, entry_id: str) -> bool:
        if entry_id in self._entries:
            del self._entries[entry_id]
            self._save()
            return True
        return False

    async def get_context_for_query(self, query: str, max_entries: int = 5) -> str:
        relevant = await self.search(query)
        relevant.sort(key=lambda e: e.access_count, reverse=True)
        relevant = relevant[:max_entries]

        if not relevant:
            return ""

        lines = []
        for entry in relevant:
            entry.access_count += 1
            lines.append(f"- {entry.key}: {entry.value}")

        self._save()
        return "\n".join(lines)


memory_store = MemoryStore()
