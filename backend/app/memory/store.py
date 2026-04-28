from app.models.schemas import MemoryEntry
import logging
from app.config import settings
import json
import math
import os
import re
import tempfile
from collections import Counter
from datetime import datetime



logger = logging.getLogger(__name__)
def _tokenize(text: str) -> list[str]:
    """Simple tokenizer: lowercase, split on non-alphanum, CJK char-level."""
    text = text.lower()
    # Split CJK characters into individual tokens
    cjk_re = re.compile(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]')
    tokens = []
    for part in re.split(r'[^a-z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+', text):
        if not part:
            continue
        if cjk_re.search(part):
            tokens.extend(list(part))
        else:
            tokens.append(part)
    return tokens


def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Cosine similarity between two sparse vectors (dicts)."""
    keys = set(vec_a) & set(vec_b)
    if not keys:
        return 0.0
    dot = sum(vec_a[k] * vec_b[k] for k in keys)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


class MemoryStore:
    def __init__(self, storage_path: str | None = None):
        self.storage_path = storage_path or settings.memory_dir
        self._entries: dict[str, MemoryEntry] = {}
        self._idf: dict[str, float] = {}
        self._entry_vectors: dict[str, dict[str, float]] = {}
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
        self._rebuild_index()

    def _save(self):
        filepath = self._filepath()
        data = [entry.model_dump(mode="json") for entry in self._entries.values()]
        dir_name = os.path.dirname(filepath)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp_path, filepath)
        except Exception as e:
            logger.debug("Suppressed error in store: %s", e)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        self._rebuild_index()

    def _rebuild_index(self):
        """Rebuild TF-IDF vectors for all entries (lightweight, no external deps)."""
        doc_tokens: dict[str, list[str]] = {}
        doc_freq: Counter = Counter()
        for eid, entry in self._entries.items():
            tokens = _tokenize(f"{entry.key} {entry.value} {entry.category}")
            doc_tokens[eid] = tokens
            unique = set(tokens)
            for t in unique:
                doc_freq[t] += 1
        n = max(len(self._entries), 1)
        self._idf = {t: math.log(n / (1 + freq)) for t, freq in doc_freq.items()}
        self._entry_vectors = {}
        for eid, tokens in doc_tokens.items():
            tf = Counter(tokens)
            total = max(len(tokens), 1)
            self._entry_vectors[eid] = {t: (count / total) * self._idf.get(t, 0) for t, count in tf.items()}

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
        """Search memories using TF-IDF cosine similarity + string match fallback."""
        if not self._entries:
            return []
        # Build query vector
        q_tokens = _tokenize(query)
        if not q_tokens:
            return list(self._entries.values())
        q_tf = Counter(q_tokens)
        q_total = max(len(q_tokens), 1)
        q_vec = {t: (count / q_total) * self._idf.get(t, 1.0) for t, count in q_tf.items()}
        # Score all entries
        scored: list[tuple[float, MemoryEntry]] = []
        query_lower = query.lower()
        for eid, entry in self._entries.items():
            vec = self._entry_vectors.get(eid, {})
            sim = _cosine_similarity(q_vec, vec)
            # Bonus for exact substring match
            if query_lower in entry.key.lower() or query_lower in entry.value.lower():
                sim += 0.3
            if sim > 0.01:
                scored.append((sim, entry))
        scored.sort(key=lambda x: -x[0])
        return [entry for _, entry in scored]

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
