import json
import logging
import time
from collections import deque
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


def _truncate_text(value: Any, *, limit: int = 200) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized_key = _truncate_text(key, limit=64)
        if value is None or isinstance(value, (bool, int, float)):
            sanitized[normalized_key] = value
            continue
        if isinstance(value, str):
            sanitized[normalized_key] = _truncate_text(value)
            continue
        if isinstance(value, (list, tuple, set)):
            items = list(value)[:10]
            sanitized[normalized_key] = [_truncate_text(item, limit=80) for item in items]
            continue
        if isinstance(value, dict):
            nested: dict[str, Any] = {}
            for nested_key, nested_value in list(value.items())[:10]:
                if nested_value is None or isinstance(nested_value, (bool, int, float)):
                    nested[_truncate_text(nested_key, limit=64)] = nested_value
                else:
                    nested[_truncate_text(nested_key, limit=64)] = _truncate_text(nested_value, limit=80)
            sanitized[normalized_key] = nested
            continue
        sanitized[normalized_key] = _truncate_text(value)
    return sanitized


class RuntimeObservability:
    def __init__(self, *, component: str, event_limit: int = 100):
        self.component = component
        self._event_limit = max(event_limit, 1)
        self._lock = Lock()
        self._backends: dict[str, dict[str, Any]] = {}
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=self._event_limit)

    def _make_bucket(self) -> dict[str, Any]:
        return {
            "count": 0,
            "success": 0,
            "failure": 0,
            "total_duration_ms": 0.0,
            "last_duration_ms": None,
            "last_event_at": None,
            "last_success_at": None,
            "last_failure_at": None,
            "last_error": "",
            "operations": {},
            "recent_events": deque(maxlen=self._event_limit),
        }

    def _get_bucket(self, backend: str) -> dict[str, Any]:
        bucket = self._backends.get(backend)
        if bucket is None:
            bucket = self._make_bucket()
            self._backends[backend] = bucket
        return bucket

    def _record_bucket(self, bucket: dict[str, Any], event: dict[str, Any]):
        bucket["count"] += 1
        bucket["success"] += 1 if event["success"] else 0
        bucket["failure"] += 0 if event["success"] else 1
        duration_ms = float(event.get("duration_ms") or 0.0)
        bucket["total_duration_ms"] += duration_ms
        bucket["last_duration_ms"] = duration_ms
        bucket["last_event_at"] = event["timestamp"]
        if event["success"]:
            bucket["last_success_at"] = event["timestamp"]
        else:
            bucket["last_failure_at"] = event["timestamp"]
            bucket["last_error"] = event.get("error") or ""
        operation = event["operation"]
        operation_bucket = bucket["operations"].get(operation)
        if operation_bucket is None:
            operation_bucket = {
                "count": 0,
                "success": 0,
                "failure": 0,
                "total_duration_ms": 0.0,
                "last_duration_ms": None,
                "last_event_at": None,
                "last_success_at": None,
                "last_failure_at": None,
                "last_error": "",
            }
            bucket["operations"][operation] = operation_bucket
        operation_bucket["count"] += 1
        operation_bucket["success"] += 1 if event["success"] else 0
        operation_bucket["failure"] += 0 if event["success"] else 1
        operation_bucket["total_duration_ms"] += duration_ms
        operation_bucket["last_duration_ms"] = duration_ms
        operation_bucket["last_event_at"] = event["timestamp"]
        if event["success"]:
            operation_bucket["last_success_at"] = event["timestamp"]
        else:
            operation_bucket["last_failure_at"] = event["timestamp"]
            operation_bucket["last_error"] = event.get("error") or ""
        bucket["recent_events"].append(event)

    def record(
        self,
        *,
        backend: str,
        operation: str,
        success: bool,
        duration_ms: float | None = None,
        thread_id: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        level: int | None = None,
    ) -> dict[str, Any]:
        event = {
            "component": self.component,
            "backend": backend,
            "operation": operation,
            "success": bool(success),
            "duration_ms": round(float(duration_ms or 0.0), 2),
            "thread_id": thread_id or None,
            "error": _truncate_text(error or "", limit=300),
            "metadata": _sanitize_metadata(metadata),
            "timestamp": time.time(),
        }
        with self._lock:
            bucket = self._get_bucket(backend)
            self._record_bucket(bucket, event)
            self._recent_events.append(event)
        log_level = level if level is not None else (logging.INFO if success else logging.WARNING)
        logger.log(
            log_level,
            "runtime_event component=%s backend=%s operation=%s success=%s duration_ms=%s thread_id=%s error=%s metadata=%s",
            self.component,
            backend,
            operation,
            event["success"],
            event["duration_ms"],
            event["thread_id"] or "-",
            event["error"] or "-",
            json.dumps(event["metadata"], ensure_ascii=False, sort_keys=True),
        )
        return event

    def _snapshot_counts(self, bucket: dict[str, Any]) -> dict[str, Any]:
        count = int(bucket.get("count") or 0)
        total_duration_ms = round(float(bucket.get("total_duration_ms") or 0.0), 2)
        return {
            "count": count,
            "success": int(bucket.get("success") or 0),
            "failure": int(bucket.get("failure") or 0),
            "success_rate": round((float(bucket.get("success") or 0) / count), 4) if count else 0.0,
            "avg_duration_ms": round(total_duration_ms / count, 2) if count else 0.0,
            "total_duration_ms": total_duration_ms,
            "last_duration_ms": bucket.get("last_duration_ms"),
            "last_event_at": bucket.get("last_event_at"),
            "last_success_at": bucket.get("last_success_at"),
            "last_failure_at": bucket.get("last_failure_at"),
            "last_error": bucket.get("last_error") or "",
        }

    def _snapshot_bucket(self, bucket: dict[str, Any] | None) -> dict[str, Any]:
        if bucket is None:
            return {
                "totals": self._snapshot_counts(self._make_bucket()),
                "operations": {},
                "recent_events": [],
            }
        operations = {
            operation: self._snapshot_counts(operation_bucket)
            for operation, operation_bucket in sorted(bucket.get("operations", {}).items())
        }
        return {
            "totals": self._snapshot_counts(bucket),
            "operations": operations,
            "recent_events": list(bucket.get("recent_events", [])),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "component": self.component,
                "backends": {
                    backend: self._snapshot_bucket(bucket)
                    for backend, bucket in sorted(self._backends.items())
                },
                "recent_events": list(self._recent_events),
            }

    def snapshot_for_backend(self, backend: str) -> dict[str, Any]:
        with self._lock:
            bucket = self._backends.get(backend)
            return self._snapshot_bucket(bucket)
