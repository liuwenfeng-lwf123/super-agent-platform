"""Demo MemoryProvider: keyword-based in-memory store.

Proof-of-concept for the Hermes single-select MemoryProvider interface.
NOT recommended for production — entries are lost on restart. Use it as a
template for wiring your own backend (Redis / Mem0 / etc.).
"""
from __future__ import annotations

import re
import time
import uuid


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "")}


class KeywordMemoryProvider:
    """Minimal provider. In-memory dict keyed by uuid."""

    def __init__(self, max_entries: int = 1000):
        self.max_entries = int(max_entries)
        self._entries: dict[str, dict] = {}

    async def add(self, key: str, value: str, metadata: dict | None = None) -> str:
        entry_id = str(uuid.uuid4())
        self._entries[entry_id] = {
            "id": entry_id,
            "key": key,
            "value": value,
            "metadata": metadata or {},
            "tokens": _tokens(f"{key} {value}"),
            "ts": time.time(),
        }
        # Evict oldest if over capacity
        if len(self._entries) > self.max_entries:
            oldest = sorted(self._entries.values(), key=lambda e: e["ts"])[0]
            self._entries.pop(oldest["id"], None)
        return entry_id

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        q = _tokens(query)
        if not q:
            return []
        scored: list[tuple[float, dict]] = []
        for entry in self._entries.values():
            overlap = len(q & entry["tokens"])
            if overlap == 0:
                continue
            score = overlap / max(len(q), 1)
            scored.append((score, entry))
        scored.sort(key=lambda t: t[0], reverse=True)
        out = []
        for score, e in scored[:limit]:
            out.append({
                "id": e["id"],
                "key": e["key"],
                "value": e["value"],
                "score": round(score, 4),
                "metadata": e["metadata"],
            })
        return out

    async def get_context_for_query(self, query: str, max_entries: int = 5) -> str:
        results = await self.search(query, limit=max_entries)
        if not results:
            return ""
        lines = ["# Recalled memories (keyword_memory provider)"]
        for r in results:
            lines.append(f"- **{r['key']}** (score={r['score']}): {r['value']}")
        return "\n".join(lines)

    async def delete(self, entry_id: str) -> bool:
        return self._entries.pop(entry_id, None) is not None
