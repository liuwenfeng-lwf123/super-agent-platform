from __future__ import annotations
import logging

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable



logger = logging.getLogger(__name__)
class EditorStateStore:
    def __init__(self):
        self._thread_states: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[str, str, dict[str, Any] | None], None]] = []

    def add_listener(self, listener: Callable[[str, str, dict[str, Any] | None], None]):
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[str, str, dict[str, Any] | None], None]):
        if listener in self._listeners:
            self._listeners.remove(listener)

    def update_state(self, thread_id: str, state: dict[str, Any], client_id: str | None = None) -> dict[str, Any]:
        thread_id = thread_id.strip()
        if not thread_id:
            raise ValueError("thread_id is required")
        if not isinstance(state, dict):
            raise ValueError("state must be an object")

        normalized = {
            "thread_id": thread_id,
            "client_id": client_id,
            "active_file": self._string_or_none(state.get("active_file")),
            "open_files": self._string_list(state.get("open_files")),
            "cursor": self._copy_mapping(state.get("cursor")),
            "selection": self._copy_mapping(state.get("selection")),
            "visible_ranges": self._copy_list_of_mappings(state.get("visible_ranges")),
            "diagnostics": self._normalize_diagnostics(state.get("diagnostics")),
            "metadata": self._copy_mapping(state.get("metadata")),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._thread_states[thread_id] = normalized
        copied = deepcopy(normalized)
        self._emit_change("updated", thread_id, copied)
        return copied

    def get_state(self, thread_id: str) -> dict[str, Any] | None:
        state = self._thread_states.get(thread_id)
        if state is None:
            return None
        return deepcopy(state)

    def clear_state(self, thread_id: str) -> bool:
        existing = self._thread_states.pop(thread_id, None)
        if existing is not None:
            self._emit_change("cleared", thread_id, None)
            return True
        return False

    def get_diagnostics(self, thread_id: str, path: str | None = None) -> list[dict[str, Any]]:
        state = self._thread_states.get(thread_id)
        if state is None:
            return []
        diagnostics = deepcopy(state.get("diagnostics", []))
        if path is None:
            return diagnostics
        return [diag for diag in diagnostics if diag.get("path") in {None, path}]

    def _string_or_none(self, value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
        return result

    def _copy_mapping(self, value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return deepcopy(value)
        return None

    def _copy_list_of_mappings(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        result: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                result.append(deepcopy(item))
        return result

    def _normalize_diagnostics(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        diagnostics: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            diagnostic = deepcopy(item)
            if not diagnostic.get("source"):
                diagnostic["source"] = "editor-state"
            diagnostics.append(diagnostic)
        return diagnostics

    def _emit_change(self, event_type: str, thread_id: str, state: dict[str, Any] | None):
        snapshot = deepcopy(state) if state is not None else None
        for listener in list(self._listeners):
            try:
                listener(event_type, thread_id, snapshot)
            except Exception as e:
                logger.debug("Suppressed error in editor_state: %s", e)
                continue


def build_editor_context_prompt(thread_id: str, *, max_open_files: int = 8, max_diagnostics: int = 5) -> str:
    if not isinstance(thread_id, str) or not thread_id.strip():
        return ""
    state = editor_state_store.get_state(thread_id)
    if state is None:
        return ""

    lines = ["## Active Editor Context"]

    if state.get("active_file"):
        lines.append(f"- Active file: {state['active_file']}")

    if state.get("client_id"):
        lines.append(f"- Source client: {state['client_id']}")

    open_files = state.get("open_files") or []
    if open_files:
        preview = ", ".join(open_files[:max_open_files])
        if len(open_files) > max_open_files:
            preview += f", ... (+{len(open_files) - max_open_files} more)"
        lines.append(f"- Open files: {preview}")

    cursor = state.get("cursor") or {}
    cursor_parts: list[str] = []
    if cursor.get("line") is not None:
        cursor_parts.append(f"line {cursor['line']}")
    if cursor.get("column") is not None:
        cursor_parts.append(f"column {cursor['column']}")
    if cursor_parts:
        lines.append(f"- Cursor: {', '.join(cursor_parts)}")

    selection = state.get("selection")
    if selection:
        lines.append(f"- Selection: {selection}")

    visible_ranges = state.get("visible_ranges") or []
    if visible_ranges:
        preview_ranges = visible_ranges[:2]
        suffix = "" if len(visible_ranges) <= 2 else f" ... (+{len(visible_ranges) - 2} more)"
        lines.append(f"- Visible ranges: {preview_ranges}{suffix}")

    diagnostics = state.get("diagnostics") or []
    if diagnostics:
        lines.append("- Recent editor diagnostics:")
        for diagnostic in diagnostics[:max_diagnostics]:
            diag_path = diagnostic.get("path") or state.get("active_file") or "unknown"
            diag_severity = diagnostic.get("severity", "info")
            diag_line = diagnostic.get("line")
            diag_column = diagnostic.get("column")
            diag_source = diagnostic.get("source")
            location = ""
            if diag_line is not None:
                location = f":{diag_line}"
                if diag_column is not None:
                    location += f":{diag_column}"
            source_suffix = f" ({diag_source})" if diag_source else ""
            lines.append(f"  - [{diag_severity}] {diag_path}{location}{source_suffix}: {diagnostic.get('message', '')}")
        if len(diagnostics) > max_diagnostics:
            lines.append(f"  - ... (+{len(diagnostics) - max_diagnostics} more diagnostics)")

    metadata = state.get("metadata") or {}
    if metadata:
        preview_items = []
        for key, value in list(metadata.items())[:5]:
            preview_items.append(f"{key}={value}")
        preview = ", ".join(preview_items)
        if len(metadata) > 5:
            preview += f", ... (+{len(metadata) - 5} more)"
        lines.append(f"- Editor metadata: {preview}")

    if state.get("updated_at"):
        lines.append(f"- State updated at: {state['updated_at']}")

    return "\n".join(lines) if len(lines) > 1 else ""


editor_state_store = EditorStateStore()
