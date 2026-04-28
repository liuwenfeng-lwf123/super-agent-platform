from __future__ import annotations
import logging

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings



logger = logging.getLogger(__name__)
@dataclass(frozen=True)
class SecurityAuditEvent:
    event_id: str
    timestamp: str
    thread_id: str
    agent_id: str
    mode: str
    tool: str
    category: str
    risk_level: str
    decision: str
    source: str
    reason: str
    matched_rule: str | None
    input: str


class SecurityAuditLog:
    def __init__(self, path: str | Path | None = None, max_entries: int = 5000):
        self.path = Path(path) if path is not None else Path(settings.data_dir) / "security_audit.jsonl"
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def append(self, event: SecurityAuditEvent) -> dict[str, Any]:
        payload = asdict(event)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._trim_locked()
        return payload

    def list_events(
        self,
        *,
        limit: int = 100,
        thread_id: str | None = None,
        tool: str | None = None,
        decision: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 1000))
        with self._lock:
            events = self._read_all_locked()
        if thread_id:
            events = [item for item in events if item.get("thread_id") == thread_id]
        if tool:
            events = [item for item in events if item.get("tool") == tool]
        if decision:
            events = [item for item in events if item.get("decision") == decision]
        return events[-limit:]

    def clear(self):
        with self._lock:
            if self.path.exists():
                self.path.unlink()

    def _read_all_locked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception as e:
                logger.debug("Suppressed error in audit: %s", e)
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def _trim_locked(self):
        events = self._read_all_locked()
        if len(events) <= self.max_entries:
            return
        keep = events[-self.max_entries :]
        self.path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in keep),
            encoding="utf-8",
        )


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_audit_event(
    *,
    thread_id: str,
    agent_id: str,
    mode: str,
    tool: str,
    category: str,
    risk_level: str,
    decision: str,
    source: str,
    reason: str,
    matched_rule: str | None,
    input_preview: str,
) -> SecurityAuditEvent:
    return SecurityAuditEvent(
        event_id=str(uuid.uuid4()),
        timestamp=utc_timestamp(),
        thread_id=thread_id,
        agent_id=agent_id,
        mode=mode,
        tool=tool,
        category=category,
        risk_level=risk_level,
        decision=decision,
        source=source,
        reason=reason,
        matched_rule=matched_rule,
        input=input_preview,
    )


security_audit_log = SecurityAuditLog()
