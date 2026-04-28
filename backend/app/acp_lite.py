from __future__ import annotations
import logging

import ast
import asyncio
from bisect import bisect_right
import io
import json
import re
import sys
import tokenize
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, BinaryIO

from app.agents import static_code_intel
from app.agents.store import thread_store
from app.local.gateway import local_gateway
from app.local.editor_state import editor_state_store
from app.runtime_backends import runtime_manager



logger = logging.getLogger(__name__)
class ACPLiteError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class ACPLiteServer:
    def __init__(self, *, store=thread_store, sandbox=runtime_manager, gateway=local_gateway, editor_state=editor_state_store):
        self.store = store
        self.sandbox = sandbox
        self.gateway = gateway
        self.editor_state = editor_state
        self._shutdown_requested = False
        self._editor_subscriptions: dict[str, dict[str, Any]] = {}
        self._pending_notifications: list[dict[str, Any]] = []
        self._notification_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.editor_state.add_listener(self._handle_editor_state_change)

    async def handle_rpc(self, request: dict[str, Any]) -> dict[str, Any] | None:
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if request.get("jsonrpc") != "2.0":
            return self._error_response(req_id, -32600, "Invalid Request", {"reason": "jsonrpc must be '2.0'"})
        if not isinstance(method, str) or not method:
            return self._error_response(req_id, -32600, "Invalid Request", {"reason": "method is required"})
        if not isinstance(params, dict):
            return self._error_response(req_id, -32602, "Invalid params", {"reason": "params must be an object"})

        try:
            result = await self._dispatch(method, params)
            if req_id is None:
                return None
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except ACPLiteError as exc:
            if req_id is None:
                return None
            return self._error_response(req_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            if req_id is None:
                return None
            return self._error_response(req_id, -32000, "Internal error", {"detail": str(exc)})

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        handlers = {
            "initialize": self._initialize,
            "shutdown": self._shutdown,
            "threads/list": self._threads_list,
            "threads/get": self._threads_get,
            "workspace/context": self._workspace_context,
            "workspace/listFiles": self._workspace_list_files,
            "workspace/readFile": self._workspace_read_file,
            "workspace/applyEdit": self._workspace_apply_edit,
            "workspace/fileHistory": self._workspace_file_history,
            "workspace/diagnostics": self._workspace_diagnostics,
            "editor/getState": self._editor_get_state,
            "editor/updateState": self._editor_update_state,
            "editor/clearState": self._editor_clear_state,
            "editor/getDiagnostics": self._editor_get_diagnostics,
            "editor/subscribe": self._editor_subscribe,
            "editor/unsubscribe": self._editor_unsubscribe,
            "editor/prepareRename": self._editor_prepare_rename,
            "editor/findReferences": self._editor_find_references,
            "editor/insertMissingSemicolons": self._editor_insert_missing_semicolons,
            "editor/removeUnusedPythonImports": self._editor_remove_unused_python_imports,
            "editor/insertPythonMissingColon": self._editor_insert_python_missing_colon,
            "editor/removeJsonComments": self._editor_remove_json_comments,
            "editor/removeJsonTrailingCommas": self._editor_remove_json_trailing_commas,
            "editor/formatDocument": self._editor_format_document,
            "editor/codeActions": self._editor_code_actions,
            "editor/resolveCodeAction": self._editor_resolve_code_action,
            "editor/applyCodeAction": self._editor_apply_code_action,
            "editor/renameSymbol": self._editor_rename_symbol,
            "local/listClients": self._local_list_clients,
            "local/bindThread": self._local_bind_thread,
            "local/unbindThread": self._local_unbind_thread,
            "local/getSystemInfo": self._local_get_system_info,
        }
        handler = handlers.get(method)
        if handler is None:
            raise ACPLiteError(-32601, f"Method not found: {method}")
        return await handler(params)

    def _capabilities(self) -> dict[str, Any]:
        return {
            "threads": {
                "list": True,
                "get": True,
            },
            "workspace": {
                "context": True,
                "listFiles": True,
                "readFile": True,
                "applyEdit": True,
                "workspaceEditPayload": True,
                "fileHistory": True,
                "diagnostics": ["python", "json"],
            },
            "editor": {
                "getState": True,
                "updateState": True,
                "clearState": True,
                "diagnostics": True,
                "subscribe": True,
                "unsubscribe": True,
                "prepareRename": True,
                "findReferences": True,
                "insertMissingSemicolons": ["js", "jsx", "ts", "tsx"],
                "removeUnusedPythonImports": True,
                "insertPythonMissingColon": True,
                "removeJsonComments": True,
                "removeJsonTrailingCommas": True,
                "formatDocument": ["json"],
                "codeActions": True,
                "resolveCodeAction": True,
                "applyCodeAction": True,
                "workspaceEditPayload": True,
                "renameSymbol": True,
                "notifications": ["editorStateChanged", "editorDiagnosticsChanged"],
            },
            "local": {
                "listClients": True,
                "bindThread": True,
                "unbindThread": True,
                "getSystemInfo": True,
            },
            "transport": {
                "stdio": True,
                "framing": ["content-length", "jsonl-input"],
            },
        }

    async def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": "acp-lite-0.1",
            "serverInfo": {"name": "super-agent-platform-acp-lite", "version": "0.1"},
            "capabilities": self._capabilities(),
            "clientInfo": params.get("clientInfo", {}),
        }

    async def _shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        self._shutdown_requested = True
        return {"ok": True}

    async def _threads_list(self, params: dict[str, Any]) -> dict[str, Any]:
        threads = await self.store.list_threads()
        return {
            "threads": [self._serialize_thread_summary(thread) for thread in threads],
        }

    async def _threads_get(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        return {
            "thread": {
                **self._serialize_thread_summary(thread),
                "messages": [
                    {
                        "id": message.id,
                        "role": message.role,
                        "content": message.content,
                        "created_at": message.created_at.isoformat(),
                        "agent_id": message.agent_id,
                        "metadata": message.metadata,
                    }
                    for message in thread.messages
                ],
            }
        }

    async def _workspace_context(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        client = self.gateway.get_client_for_thread(thread.id)
        runtime_info = None
        if hasattr(self.sandbox, "get_thread_runtime"):
            try:
                runtime_info = self.sandbox.get_thread_runtime(thread.id)
            except Exception as e:
                logger.debug("Suppressed error in acp_lite: %s", e)
                runtime_info = None
        return {
            "thread": self._serialize_thread_summary(thread),
            "workspace": {
                "workspace_dir": self.sandbox.get_workspace_dir(thread.id),
                "outputs_dir": self.sandbox.get_outputs_dir(thread.id),
                "uploads_dir": self.sandbox.get_uploads_dir(thread.id),
            },
            "runtime": runtime_info,
            "editor_state": self.editor_state.get_state(thread.id),
            "local_client": None if client is None else {
                "client_id": client.client_id,
                "info": dict(client.info),
            },
        }

    async def _workspace_list_files(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        path = str(params.get("path", "."))
        result = await self.sandbox.list_files(path, thread_id=thread.id)
        if not result.get("success"):
            raise ACPLiteError(-32004, result.get("error", "Failed to list files"), {"path": path})
        return result

    async def _workspace_read_file(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        path = self._require_string(params, "path")
        result = await self.sandbox.read_file(path, thread_id=thread.id)
        if not result.get("success"):
            raise ACPLiteError(-32004, result.get("error", "Failed to read file"), {"path": path})
        return result

    async def _workspace_apply_edit(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        workspace_edit = params.get("workspace_edit")
        if workspace_edit is not None:
            applied = await self._apply_workspace_edit_payload(thread.id, workspace_edit)
            return {"thread_id": thread.id, "applied": applied, "workspace_edit": workspace_edit}
        edits = params.get("edits")
        if edits is None:
            edits = [{key: value for key, value in params.items() if key != "thread_id"}]
        if not isinstance(edits, list) or not edits:
            raise ACPLiteError(-32602, "Invalid params", {"reason": "edits must be a non-empty array"})

        applied: list[dict[str, Any]] = []
        for edit in edits:
            if not isinstance(edit, dict):
                raise ACPLiteError(-32602, "Invalid params", {"reason": "each edit must be an object"})
            path = self._require_string(edit, "path")
            current_result = await self.sandbox.read_file(path, thread_id=thread.id)
            current_text = current_result.get("content", "") if current_result.get("success") else ""
            new_content = self._materialize_edit(current_text, edit)
            write_result = await self.sandbox.write_file(path, new_content, thread_id=thread.id)
            if not write_result.get("success"):
                raise ACPLiteError(-32000, write_result.get("error", "Failed to apply edit"), {"path": path})
            applied.append({
                "path": path,
                "size": write_result.get("size", len(new_content)),
                "mode": "replace" if "content" in edit else "range",
            })
        return {"thread_id": thread.id, "applied": applied}

    async def _workspace_file_history(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        path = params.get("path")
        limit = int(params.get("limit", 50))
        entries = self.sandbox.get_file_history(thread.id, path=path, limit=limit)
        return {"thread_id": thread.id, "entries": entries, "count": len(entries)}

    async def _workspace_diagnostics(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        path = self._require_string(params, "path")
        result = await self.sandbox.read_file(path, thread_id=thread.id)
        if not result.get("success"):
            raise ACPLiteError(-32004, result.get("error", "Failed to read file"), {"path": path})
        content = result.get("content", "")
        diagnostics = self._merge_diagnostics(
            self.editor_state.get_diagnostics(thread.id, path=path),
            self._compute_diagnostics(path, content),
        )
        return {"thread_id": thread.id, "path": path, "diagnostics": diagnostics}

    async def _editor_get_state(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        return {"thread_id": thread.id, "editor_state": self.editor_state.get_state(thread.id)}

    async def _editor_update_state(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        state = params.get("state")
        if not isinstance(state, dict):
            raise ACPLiteError(-32602, "Invalid params", {"reason": "state must be an object"})
        updated = self.editor_state.update_state(thread.id, state, client_id=params.get("client_id"))
        return {"thread_id": thread.id, "editor_state": updated}

    async def _editor_clear_state(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        cleared = self.editor_state.clear_state(thread.id)
        return {"thread_id": thread.id, "cleared": cleared}

    async def _editor_get_diagnostics(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        path = params.get("path")
        if path is not None and not isinstance(path, str):
            raise ACPLiteError(-32602, "Invalid params", {"reason": "path must be a string"})
        diagnostics = self.editor_state.get_diagnostics(thread.id, path=path)
        return {"thread_id": thread.id, "diagnostics": diagnostics, "path": path}

    async def _editor_subscribe(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = params.get("thread_id")
        if thread_id is not None:
            await self._require_thread(params)
        events = params.get("events") or ["editorStateChanged", "editorDiagnosticsChanged"]
        if not isinstance(events, list) or not events:
            raise ACPLiteError(-32602, "Invalid params", {"reason": "events must be a non-empty array"})
        normalized_events = []
        for event_name in events:
            if event_name not in {"editorStateChanged", "editorDiagnosticsChanged"}:
                raise ACPLiteError(-32602, "Invalid params", {"reason": f"unsupported event '{event_name}'"})
            if event_name not in normalized_events:
                normalized_events.append(event_name)
        subscription_id = uuid.uuid4().hex
        self._editor_subscriptions[subscription_id] = {
            "thread_id": thread_id,
            "events": normalized_events,
        }
        return {
            "subscription_id": subscription_id,
            "thread_id": thread_id,
            "events": normalized_events,
        }

    async def _editor_unsubscribe(self, params: dict[str, Any]) -> dict[str, Any]:
        subscription_id = self._require_string(params, "subscription_id")
        removed = self._editor_subscriptions.pop(subscription_id, None) is not None
        return {"subscription_id": subscription_id, "unsubscribed": removed}

    async def _editor_prepare_rename(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        path, target_path, _workspace_root, symbol, occurrences = self._inspect_editor_symbol(thread.id, params)
        line = self._require_positive_int(params, "line")
        column = self._require_non_negative_int(params, "column")
        source = target_path.read_text(encoding="utf-8", errors="replace")
        symbol_match = self._symbol_match_at_position(source, line, column)
        if symbol_match is None or not symbol:
            return {
                "thread_id": thread.id,
                "path": path,
                "can_rename": False,
            }
        _symbol_name, start_column, end_column = symbol_match
        return {
            "thread_id": thread.id,
            "path": path,
            "can_rename": True,
            "symbol": symbol,
            "placeholder": symbol,
            "range": {
                "start": {"line": line, "column": start_column},
                "end": {"line": line, "column": end_column},
            },
            "reference_count": len(occurrences),
        }

    async def _editor_find_references(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        path, _target_path, workspace_root, symbol, occurrences = self._inspect_editor_symbol(thread.id, params)
        if not symbol:
            raise ACPLiteError(-32004, "No symbol found at position", {"path": path, "line": params.get("line"), "column": params.get("column")})
        references = self._serialize_references(occurrences, workspace_root)
        return {
            "thread_id": thread.id,
            "path": path,
            "symbol": symbol,
            "references": references,
            "count": len(references),
        }

    async def _editor_insert_missing_semicolons(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        plan = await self._plan_insert_missing_semicolons(thread.id, params)
        workspace_edit = self._build_missing_semicolon_workspace_edit(plan)
        if not plan["changed"]:
            return {
                "thread_id": thread.id,
                "path": plan["path"],
                "changed": False,
                "fixes_applied": 0,
                "diagnostic_count": plan["diagnostic_count"],
                "workspace_edit": workspace_edit,
            }
        write_result = await self.sandbox.write_file(plan["path"], plan["content"], thread_id=thread.id)
        if not write_result.get("success"):
            raise ACPLiteError(-32000, write_result.get("error", "Failed to apply semicolon quick fix"), {"path": plan["path"]})
        return {
            "thread_id": thread.id,
            "path": plan["path"],
            "changed": True,
            "fixes_applied": len(plan["fixes"]),
            "diagnostic_count": plan["diagnostic_count"],
            "workspace_edit": workspace_edit,
        }

    async def _editor_remove_unused_python_imports(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        plan = await self._plan_remove_unused_python_imports(thread.id, params)
        workspace_edit = self._build_unused_python_import_workspace_edit(plan)
        if not plan["changed"]:
            return {
                "thread_id": thread.id,
                "path": plan["path"],
                "changed": False,
                "fixes_applied": 0,
                "diagnostic_count": plan["diagnostic_count"],
                "workspace_edit": workspace_edit,
            }
        write_result = await self.sandbox.write_file(plan["path"], plan["content"], thread_id=thread.id)
        if not write_result.get("success"):
            raise ACPLiteError(-32000, write_result.get("error", "Failed to remove unused Python imports"), {"path": plan["path"]})
        return {
            "thread_id": thread.id,
            "path": plan["path"],
            "changed": True,
            "fixes_applied": len(plan["fixes"]),
            "diagnostic_count": plan["diagnostic_count"],
            "workspace_edit": workspace_edit,
        }

    async def _editor_insert_python_missing_colon(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        plan = await self._plan_insert_python_missing_colon(thread.id, params)
        workspace_edit = self._build_python_missing_colon_workspace_edit(plan)
        if not plan["changed"]:
            return {
                "thread_id": thread.id,
                "path": plan["path"],
                "changed": False,
                "fixes_applied": 0,
                "diagnostic_count": plan["diagnostic_count"],
                "workspace_edit": workspace_edit,
            }
        write_result = await self.sandbox.write_file(plan["path"], plan["content"], thread_id=thread.id)
        if not write_result.get("success"):
            raise ACPLiteError(-32000, write_result.get("error", "Failed to apply Python quick fix"), {"path": plan["path"]})
        return {
            "thread_id": thread.id,
            "path": plan["path"],
            "changed": True,
            "fixes_applied": len(plan["fixes"]),
            "diagnostic_count": plan["diagnostic_count"],
            "workspace_edit": workspace_edit,
        }

    async def _editor_remove_json_comments(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        plan = await self._plan_remove_json_comments(thread.id, params)
        workspace_edit = self._build_json_comment_workspace_edit(plan)
        if not plan["changed"]:
            return {
                "thread_id": thread.id,
                "path": plan["path"],
                "changed": False,
                "fixes_applied": 0,
                "diagnostic_count": plan["diagnostic_count"],
                "workspace_edit": workspace_edit,
            }
        write_result = await self.sandbox.write_file(plan["path"], plan["content"], thread_id=thread.id)
        if not write_result.get("success"):
            raise ACPLiteError(-32000, write_result.get("error", "Failed to apply JSON quick fix"), {"path": plan["path"]})
        return {
            "thread_id": thread.id,
            "path": plan["path"],
            "changed": True,
            "fixes_applied": len(plan["fixes"]),
            "diagnostic_count": plan["diagnostic_count"],
            "workspace_edit": workspace_edit,
        }

    async def _editor_remove_json_trailing_commas(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        plan = await self._plan_remove_json_trailing_commas(thread.id, params)
        workspace_edit = self._build_json_trailing_comma_workspace_edit(plan)
        if not plan["changed"]:
            return {
                "thread_id": thread.id,
                "path": plan["path"],
                "changed": False,
                "fixes_applied": 0,
                "diagnostic_count": plan["diagnostic_count"],
                "workspace_edit": workspace_edit,
            }
        write_result = await self.sandbox.write_file(plan["path"], plan["content"], thread_id=thread.id)
        if not write_result.get("success"):
            raise ACPLiteError(-32000, write_result.get("error", "Failed to apply JSON quick fix"), {"path": plan["path"]})
        return {
            "thread_id": thread.id,
            "path": plan["path"],
            "changed": True,
            "fixes_applied": len(plan["fixes"]),
            "diagnostic_count": plan["diagnostic_count"],
            "workspace_edit": workspace_edit,
        }

    async def _editor_format_document(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        plan = await self._plan_document_format(thread.id, params)
        workspace_edit = self._build_document_format_workspace_edit(plan)
        if not plan["changed"]:
            return {
                "thread_id": thread.id,
                "path": plan["path"],
                "changed": False,
                "size": plan["new_size"],
                "workspace_edit": workspace_edit,
            }
        write_result = await self.sandbox.write_file(plan["path"], plan["content"], thread_id=thread.id)
        if not write_result.get("success"):
            raise ACPLiteError(-32000, write_result.get("error", "Failed to format document"), {"path": plan["path"]})
        return {
            "thread_id": thread.id,
            "path": plan["path"],
            "changed": True,
            "size": write_result.get("size", plan["new_size"]),
            "workspace_edit": workspace_edit,
        }

    async def _editor_code_actions(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        path, target_path, workspace_root = self._resolve_editor_target(thread.id, params)
        actions: list[dict[str, Any]] = []
        only_filters = self._resolve_code_action_only_filters(params)
        requested_diagnostics = self._resolve_code_action_context_diagnostics(params, path)
        quickfix_arguments = {
            "thread_id": thread.id,
            "path": path,
        }
        if requested_diagnostics is not None:
            quickfix_arguments["diagnostics"] = requested_diagnostics

        position = self._get_optional_position(params)
        if position is not None and self._supports_symbol_actions(target_path) and self._action_kind_matches_filters("refactor.rename", only_filters):
            line, column = position
            symbol, occurrences = self._collect_symbol_occurrences(target_path, line, column, workspace_root)
            if symbol:
                references = self._serialize_references(occurrences, workspace_root)
                actions.append({
                    "kind": "refactor.rename",
                    "title": f"Rename `{symbol}` ({len(references)} references)",
                    "command": "editor/renameSymbol",
                    "arguments": {
                        "thread_id": thread.id,
                        "path": path,
                        "line": line,
                        "column": column,
                    },
                    "input_schema": {
                        "type": "object",
                        "required": ["new_name"],
                        "properties": {
                            "new_name": {
                                "type": "string",
                                "description": f"New identifier name for `{symbol}`",
                            },
                        },
                    },
                    "data": {
                        "symbol": symbol,
                        "reference_count": len(references),
                    },
                })

        if self._supports_missing_semicolon_actions(target_path) and self._action_kind_matches_filters("quickfix.script.insertMissingSemicolons", only_filters):
            try:
                semicolon_plan = await self._plan_insert_missing_semicolons(thread.id, dict(quickfix_arguments))
            except ACPLiteError:
                semicolon_plan = None
            if semicolon_plan is not None and semicolon_plan["changed"]:
                actions.append({
                    "kind": "quickfix.script.insertMissingSemicolons",
                    "title": f"Insert missing semicolons ({len(semicolon_plan['fixes'])} fixes)",
                    "command": "editor/insertMissingSemicolons",
                    "arguments": dict(quickfix_arguments),
                    "data": {
                        "language": target_path.suffix.lower().lstrip("."),
                        "fix_count": len(semicolon_plan["fixes"]),
                        "diagnostic_count": semicolon_plan["diagnostic_count"],
                    },
                })

        if target_path.suffix.lower() == ".py":
            if self._action_kind_matches_filters("quickfix.python.removeUnusedImports", only_filters):
                try:
                    unused_import_plan = await self._plan_remove_unused_python_imports(thread.id, dict(quickfix_arguments))
                except ACPLiteError:
                    unused_import_plan = None
                if unused_import_plan is not None and unused_import_plan["changed"]:
                    actions.append({
                        "kind": "quickfix.python.removeUnusedImports",
                        "title": f"Remove unused imports ({len(unused_import_plan['fixes'])} fixes)",
                        "command": "editor/removeUnusedPythonImports",
                        "arguments": dict(quickfix_arguments),
                        "data": {
                            "language": "python",
                            "fix_count": len(unused_import_plan["fixes"]),
                            "diagnostic_count": unused_import_plan["diagnostic_count"],
                        },
                    })
            if self._action_kind_matches_filters("quickfix.python.insertMissingColon", only_filters):
                try:
                    python_colon_plan = await self._plan_insert_python_missing_colon(thread.id, dict(quickfix_arguments))
                except ACPLiteError:
                    python_colon_plan = None
                if python_colon_plan is not None and python_colon_plan["changed"]:
                    actions.append({
                        "kind": "quickfix.python.insertMissingColon",
                        "title": f"Insert missing colon ({len(python_colon_plan['fixes'])} fix)",
                        "command": "editor/insertPythonMissingColon",
                        "arguments": dict(quickfix_arguments),
                        "data": {
                            "language": "python",
                            "fix_count": len(python_colon_plan["fixes"]),
                            "diagnostic_count": python_colon_plan["diagnostic_count"],
                        },
                    })

        if target_path.suffix.lower() == ".json":
            if self._action_kind_matches_filters("quickfix.json.removeComments", only_filters):
                try:
                    comment_plan = await self._plan_remove_json_comments(thread.id, dict(quickfix_arguments))
                except ACPLiteError:
                    comment_plan = None
                if comment_plan is not None and comment_plan["changed"]:
                    actions.append({
                        "kind": "quickfix.json.removeComments",
                        "title": f"Remove JSON comments ({len(comment_plan['fixes'])} fixes)",
                        "command": "editor/removeJsonComments",
                        "arguments": dict(quickfix_arguments),
                        "data": {
                            "language": "json",
                            "fix_count": len(comment_plan["fixes"]),
                            "diagnostic_count": comment_plan["diagnostic_count"],
                        },
                    })
            if self._action_kind_matches_filters("quickfix.json.removeTrailingCommas", only_filters):
                try:
                    trailing_comma_plan = await self._plan_remove_json_trailing_commas(thread.id, dict(quickfix_arguments))
                except ACPLiteError:
                    trailing_comma_plan = None
                if trailing_comma_plan is not None and trailing_comma_plan["changed"]:
                    actions.append({
                        "kind": "quickfix.json.removeTrailingCommas",
                        "title": f"Remove trailing commas ({len(trailing_comma_plan['fixes'])} fixes)",
                        "command": "editor/removeJsonTrailingCommas",
                        "arguments": dict(quickfix_arguments),
                        "data": {
                            "language": "json",
                            "fix_count": len(trailing_comma_plan["fixes"]),
                            "diagnostic_count": trailing_comma_plan["diagnostic_count"],
                        },
                    })
            if self._action_kind_matches_filters("source.formatDocument", only_filters):
                try:
                    format_plan = await self._plan_document_format(thread.id, {"path": path, **({"indent": params["indent"]} if "indent" in params else {})})
                except ACPLiteError:
                    format_plan = None
                if format_plan is not None and format_plan["changed"]:
                    actions.append({
                        "kind": "source.formatDocument",
                        "title": "Format JSON document",
                        "command": "editor/formatDocument",
                        "arguments": {
                            "thread_id": thread.id,
                            "path": path,
                        },
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "indent": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 8,
                                    "default": 2,
                                    "description": "Indentation width for formatted JSON",
                                },
                            },
                        },
                        "data": {
                            "language": "json",
                            "changed": True,
                        },
                    })
        if only_filters:
            actions = [action for action in actions if self._action_kind_matches_filters(action.get("kind"), only_filters)]
        return {"thread_id": thread.id, "path": path, "actions": actions}

    async def _editor_resolve_code_action(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        action, command, merged = self._normalize_editor_action_request(thread.id, params)
        if command == "editor/renameSymbol":
            new_name = self._require_string(merged, "new_name")
            plan = await self._plan_symbol_rename(thread.id, merged, new_name)
            workspace_edit = self._build_symbol_workspace_edit(plan)
            preview = {
                "path": plan["path"],
                "symbol": plan["symbol"],
                "new_name": plan["new_name"],
                "files_changed": plan["files_changed"],
                "occurrences_changed": plan["occurrences_changed"],
                "edits": [
                    {
                        "path": item["path"],
                        "occurrences": item["occurrences"],
                        "edits": item["edits"],
                    }
                    for item in plan["planned_files"]
                ],
                "workspace_edit": workspace_edit,
            }
        elif command == "editor/removeUnusedPythonImports":
            plan = await self._plan_remove_unused_python_imports(thread.id, merged)
            workspace_edit = self._build_unused_python_import_workspace_edit(plan)
            preview = {
                "path": plan["path"],
                "changed": plan["changed"],
                "diagnostic_count": plan["diagnostic_count"],
                "fixes": plan["fixes"],
                "workspace_edit": workspace_edit,
            }
        elif command == "editor/insertMissingSemicolons":
            plan = await self._plan_insert_missing_semicolons(thread.id, merged)
            workspace_edit = self._build_missing_semicolon_workspace_edit(plan)
            preview = {
                "path": plan["path"],
                "changed": plan["changed"],
                "diagnostic_count": plan["diagnostic_count"],
                "fixes": plan["fixes"],
                "workspace_edit": workspace_edit,
            }
        elif command == "editor/insertPythonMissingColon":
            plan = await self._plan_insert_python_missing_colon(thread.id, merged)
            workspace_edit = self._build_python_missing_colon_workspace_edit(plan)
            preview = {
                "path": plan["path"],
                "changed": plan["changed"],
                "diagnostic_count": plan["diagnostic_count"],
                "fixes": plan["fixes"],
                "workspace_edit": workspace_edit,
            }
        elif command == "editor/removeJsonComments":
            plan = await self._plan_remove_json_comments(thread.id, merged)
            workspace_edit = self._build_json_comment_workspace_edit(plan)
            preview = {
                "path": plan["path"],
                "changed": plan["changed"],
                "diagnostic_count": plan["diagnostic_count"],
                "fixes": plan["fixes"],
                "workspace_edit": workspace_edit,
            }
        elif command == "editor/removeJsonTrailingCommas":
            plan = await self._plan_remove_json_trailing_commas(thread.id, merged)
            workspace_edit = self._build_json_trailing_comma_workspace_edit(plan)
            preview = {
                "path": plan["path"],
                "changed": plan["changed"],
                "diagnostic_count": plan["diagnostic_count"],
                "fixes": plan["fixes"],
                "workspace_edit": workspace_edit,
            }
        elif command == "editor/formatDocument":
            plan = await self._plan_document_format(thread.id, merged)
            workspace_edit = self._build_document_format_workspace_edit(plan)
            preview = {
                "path": plan["path"],
                "changed": plan["changed"],
                "files_changed": 1 if plan["changed"] else 0,
                "edits": [] if not plan["changed"] else [{
                    "path": plan["path"],
                    "mode": "replaceDocument",
                    "old_size": plan["old_size"],
                    "new_size": plan["new_size"],
                }],
                "workspace_edit": workspace_edit,
            }
        else:
            raise ACPLiteError(-32602, "Invalid params", {"reason": f"unsupported action command '{command}'"})
        return {
            "thread_id": thread.id,
            "resolved": True,
            "command": command,
            "action": action,
            "preview": preview,
        }

    async def _editor_apply_code_action(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        _action, command, merged = self._normalize_editor_action_request(thread.id, params)
        apply_mode = self._resolve_apply_mode(params)
        if apply_mode == "server":
            if command == "editor/renameSymbol":
                result = await self._editor_rename_symbol(merged)
            elif command == "editor/removeUnusedPythonImports":
                result = await self._editor_remove_unused_python_imports(merged)
            elif command == "editor/insertMissingSemicolons":
                result = await self._editor_insert_missing_semicolons(merged)
            elif command == "editor/insertPythonMissingColon":
                result = await self._editor_insert_python_missing_colon(merged)
            elif command == "editor/removeJsonComments":
                result = await self._editor_remove_json_comments(merged)
            elif command == "editor/removeJsonTrailingCommas":
                result = await self._editor_remove_json_trailing_commas(merged)
            elif command == "editor/formatDocument":
                result = await self._editor_format_document(merged)
            else:
                raise ACPLiteError(-32602, "Invalid params", {"reason": f"unsupported action command '{command}'"})
        elif apply_mode == "workspace_edit":
            if command == "editor/renameSymbol":
                new_name = self._require_string(merged, "new_name")
                plan = await self._plan_symbol_rename(thread.id, merged, new_name)
                workspace_edit = self._build_symbol_workspace_edit(plan)
                result = {
                    "thread_id": thread.id,
                    "symbol": plan["symbol"],
                    "new_name": plan["new_name"],
                    "files_changed": plan["files_changed"],
                    "occurrences_changed": plan["occurrences_changed"],
                    "applied": [],
                    "workspace_edit": workspace_edit,
                }
            elif command == "editor/removeUnusedPythonImports":
                plan = await self._plan_remove_unused_python_imports(thread.id, merged)
                workspace_edit = self._build_unused_python_import_workspace_edit(plan)
                result = {
                    "thread_id": thread.id,
                    "path": plan["path"],
                    "changed": plan["changed"],
                    "fixes_applied": len(plan["fixes"]),
                    "diagnostic_count": plan["diagnostic_count"],
                    "workspace_edit": workspace_edit,
                }
            elif command == "editor/insertMissingSemicolons":
                plan = await self._plan_insert_missing_semicolons(thread.id, merged)
                workspace_edit = self._build_missing_semicolon_workspace_edit(plan)
                result = {
                    "thread_id": thread.id,
                    "path": plan["path"],
                    "changed": plan["changed"],
                    "fixes_applied": len(plan["fixes"]),
                    "diagnostic_count": plan["diagnostic_count"],
                    "workspace_edit": workspace_edit,
                }
            elif command == "editor/insertPythonMissingColon":
                plan = await self._plan_insert_python_missing_colon(thread.id, merged)
                workspace_edit = self._build_python_missing_colon_workspace_edit(plan)
                result = {
                    "thread_id": thread.id,
                    "path": plan["path"],
                    "changed": plan["changed"],
                    "fixes_applied": len(plan["fixes"]),
                    "diagnostic_count": plan["diagnostic_count"],
                    "workspace_edit": workspace_edit,
                }
            elif command == "editor/removeJsonComments":
                plan = await self._plan_remove_json_comments(thread.id, merged)
                workspace_edit = self._build_json_comment_workspace_edit(plan)
                result = {
                    "thread_id": thread.id,
                    "path": plan["path"],
                    "changed": plan["changed"],
                    "fixes_applied": len(plan["fixes"]),
                    "diagnostic_count": plan["diagnostic_count"],
                    "workspace_edit": workspace_edit,
                }
            elif command == "editor/removeJsonTrailingCommas":
                plan = await self._plan_remove_json_trailing_commas(thread.id, merged)
                workspace_edit = self._build_json_trailing_comma_workspace_edit(plan)
                result = {
                    "thread_id": thread.id,
                    "path": plan["path"],
                    "changed": plan["changed"],
                    "fixes_applied": len(plan["fixes"]),
                    "diagnostic_count": plan["diagnostic_count"],
                    "workspace_edit": workspace_edit,
                }
            elif command == "editor/formatDocument":
                plan = await self._plan_document_format(thread.id, merged)
                workspace_edit = self._build_document_format_workspace_edit(plan)
                result = {
                    "thread_id": thread.id,
                    "path": plan["path"],
                    "changed": plan["changed"],
                    "size": plan["new_size"],
                    "workspace_edit": workspace_edit,
                }
            else:
                raise ACPLiteError(-32602, "Invalid params", {"reason": f"unsupported action command '{command}'"})
        else:
            raise ACPLiteError(-32602, "Invalid params", {"reason": f"unsupported apply_mode '{apply_mode}'"})
        return {
            "thread_id": thread.id,
            "applied": apply_mode == "server",
            "apply_mode": apply_mode,
            "command": command,
            "result": result,
            "workspace_edit": result.get("workspace_edit"),
        }

    async def _editor_rename_symbol(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        new_name = self._require_string(params, "new_name")
        plan = await self._plan_symbol_rename(thread.id, params, new_name)
        workspace_edit = self._build_symbol_workspace_edit(plan)
        applied: list[dict[str, Any]] = []
        for planned_file in plan["planned_files"]:
            write_result = await self.sandbox.write_file(planned_file["path"], planned_file["content"], thread_id=thread.id)
            if not write_result.get("success"):
                raise ACPLiteError(-32000, write_result.get("error", "Failed to rename symbol"), {"path": planned_file["path"]})
            applied.append({
                "path": planned_file["path"],
                "occurrences": planned_file["occurrences"],
                "size": write_result.get("size", planned_file["size"]),
            })

        return {
            "thread_id": thread.id,
            "symbol": plan["symbol"],
            "new_name": plan["new_name"],
            "files_changed": plan["files_changed"],
            "occurrences_changed": plan["occurrences_changed"],
            "applied": applied,
            "workspace_edit": workspace_edit,
        }

    async def _local_list_clients(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"clients": self.gateway.list_clients()}

    async def _local_bind_thread(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        client_id = self._require_string(params, "client_id")
        if self.gateway.get_client(client_id) is None:
            raise ACPLiteError(-32004, "Local client not found", {"client_id": client_id})
        self.gateway.bind_thread(thread.id, client_id)
        return {"thread_id": thread.id, "client_id": client_id, "status": "bound"}

    async def _local_unbind_thread(self, params: dict[str, Any]) -> dict[str, Any]:
        thread = await self._require_thread(params)
        self.gateway.unbind_thread(thread.id)
        return {"thread_id": thread.id, "status": "unbound"}

    async def _local_get_system_info(self, params: dict[str, Any]) -> dict[str, Any]:
        client = None
        client_id = params.get("client_id")
        if client_id:
            client = self.gateway.get_client(str(client_id))
        else:
            thread = await self._require_thread(params)
            client = self.gateway.get_client_for_thread(thread.id)
        if client is None:
            raise ACPLiteError(-32004, "No local client available")
        result = await client.send_request("get_system_info", {})
        if not result.get("success"):
            raise ACPLiteError(-32000, result.get("error", "Failed to get system info"))
        return result

    async def serve_forever(self, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None):
        input_stream = stdin or sys.stdin.buffer
        output_stream = stdout or sys.stdout.buffer
        self._loop = asyncio.get_running_loop()
        self._notification_event = asyncio.Event()
        read_task = asyncio.create_task(asyncio.to_thread(self._read_message, input_stream))
        notify_task = asyncio.create_task(self._notification_event.wait())
        try:
            while not self._shutdown_requested:
                done, _ = await asyncio.wait({read_task, notify_task}, return_when=asyncio.FIRST_COMPLETED)

                if notify_task in done:
                    notify_task.result()
                    for notification in self._drain_notifications():
                        self._write_message(output_stream, notification)
                        output_stream.flush()
                    if self._notification_event is not None:
                        self._notification_event.clear()
                    notify_task = asyncio.create_task(self._notification_event.wait())

                if read_task in done:
                    request = read_task.result()
                    if request is None:
                        break
                    response = await self.handle_rpc(request)
                    if response is not None:
                        self._write_message(output_stream, response)
                        output_stream.flush()
                    for notification in self._drain_notifications():
                        self._write_message(output_stream, notification)
                        output_stream.flush()
                    read_task = asyncio.create_task(asyncio.to_thread(self._read_message, input_stream))
        finally:
            read_task.cancel()
            notify_task.cancel()
            await asyncio.gather(read_task, notify_task, return_exceptions=True)
            self.editor_state.remove_listener(self._handle_editor_state_change)

    def _read_message(self, stream: BinaryIO) -> dict[str, Any] | None:
        first_line = stream.readline()
        if not first_line:
            return None
        if first_line.startswith(b"Content-Length:"):
            try:
                content_length = int(first_line.split(b":", 1)[1].strip())
            except ValueError as exc:
                raise ACPLiteError(-32700, "Parse error", {"detail": str(exc)}) from exc
            while True:
                header_line = stream.readline()
                if not header_line or header_line in {b"\n", b"\r\n"}:
                    break
            payload = stream.read(content_length)
            if not payload:
                return None
            return json.loads(payload.decode("utf-8"))
        line = first_line.strip()
        while not line:
            next_line = stream.readline()
            if not next_line:
                return None
            line = next_line.strip()
        return json.loads(line.decode("utf-8"))

    def _write_message(self, stream: BinaryIO, payload: dict[str, Any]):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stream.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("utf-8"))
        stream.write(encoded)

    async def _require_thread(self, params: dict[str, Any]):
        thread_id = self._require_string(params, "thread_id")
        thread = await self.store.get(thread_id)
        if thread is None:
            raise ACPLiteError(-32004, "Thread not found", {"thread_id": thread_id})
        return thread

    def _require_string(self, params: dict[str, Any], key: str) -> str:
        value = params.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ACPLiteError(-32602, "Invalid params", {"reason": f"{key} must be a non-empty string"})
        return value.strip()

    def _serialize_thread_summary(self, thread) -> dict[str, Any]:
        return {
            "id": thread.id,
            "title": thread.title,
            "message_count": len(thread.messages),
            "created_at": thread.created_at.isoformat(),
            "updated_at": thread.updated_at.isoformat(),
            "parent_id": thread.parent_id,
            "compact_summary": thread.compact_summary,
        }

    def _materialize_edit(self, current_text: str, edit: dict[str, Any]) -> str:
        if "content" in edit:
            return str(edit.get("content", ""))
        if "new_text" not in edit:
            raise ACPLiteError(-32602, "Invalid params", {"reason": "edit must contain content or new_text"})
        start_line = int(edit.get("start_line", 1))
        end_line = int(edit.get("end_line", start_line))
        if start_line < 1 or end_line < start_line:
            raise ACPLiteError(-32602, "Invalid params", {"reason": "invalid start_line/end_line"})
        lines = current_text.splitlines(keepends=True)
        start_index = min(start_line - 1, len(lines))
        end_index = min(end_line, len(lines))
        return "".join(lines[:start_index]) + str(edit.get("new_text", "")) + "".join(lines[end_index:])

    async def _apply_workspace_edit_payload(self, thread_id: str, workspace_edit: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(workspace_edit, dict):
            raise ACPLiteError(-32602, "Invalid params", {"reason": "workspace_edit must be an object"})
        document_changes = workspace_edit.get("document_changes")
        if not isinstance(document_changes, list) or not document_changes:
            raise ACPLiteError(-32602, "Invalid params", {"reason": "workspace_edit.document_changes must be a non-empty array"})
        workspace_root = Path(self.sandbox.get_workspace_dir(thread_id)).resolve()
        applied: list[dict[str, Any]] = []
        for change in document_changes:
            if not isinstance(change, dict):
                raise ACPLiteError(-32602, "Invalid params", {"reason": "each workspace_edit change must be an object"})
            kind = self._require_string(change, "kind")
            path = self._normalize_workspace_edit_path(workspace_root, change)
            if kind == "replaceDocument":
                content = change.get("content")
                if not isinstance(content, str):
                    raise ACPLiteError(-32602, "Invalid params", {"reason": "replaceDocument changes must contain string content", "path": path})
                write_result = await self.sandbox.write_file(path, content, thread_id=thread_id)
                if not write_result.get("success"):
                    raise ACPLiteError(-32000, write_result.get("error", "Failed to apply workspace edit"), {"path": path})
                applied.append({
                    "path": path,
                    "size": write_result.get("size", len(content)),
                    "mode": kind,
                })
                continue
            if kind == "textEdits":
                edits = change.get("edits")
                if not isinstance(edits, list) or not edits:
                    raise ACPLiteError(-32602, "Invalid params", {"reason": "textEdits changes must contain a non-empty edits array", "path": path})
                read_result = await self.sandbox.read_file(path, thread_id=thread_id)
                if not read_result.get("success"):
                    raise ACPLiteError(-32004, read_result.get("error", "Failed to read file"), {"path": path})
                updated_content, edit_count = self._apply_workspace_text_edits(read_result.get("content", ""), edits, path)
                write_result = await self.sandbox.write_file(path, updated_content, thread_id=thread_id)
                if not write_result.get("success"):
                    raise ACPLiteError(-32000, write_result.get("error", "Failed to apply workspace edit"), {"path": path})
                applied.append({
                    "path": path,
                    "size": write_result.get("size", len(updated_content)),
                    "mode": kind,
                    "edits": edit_count,
                })
                continue
            raise ACPLiteError(-32602, "Invalid params", {"reason": f"unsupported workspace_edit change kind '{kind}'", "path": path})
        return applied

    def _apply_workspace_text_edits(self, current_text: str, edits: list[dict[str, Any]], path: str) -> tuple[str, int]:
        line_starts = self._build_line_starts(current_text)
        materialized: list[dict[str, Any]] = []
        for edit in edits:
            if not isinstance(edit, dict):
                raise ACPLiteError(-32602, "Invalid params", {"reason": "each text edit must be an object", "path": path})
            range_payload = edit.get("range")
            if not isinstance(range_payload, dict):
                raise ACPLiteError(-32602, "Invalid params", {"reason": "text edits must contain a range object", "path": path})
            start = range_payload.get("start")
            end = range_payload.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                raise ACPLiteError(-32602, "Invalid params", {"reason": "range.start and range.end must be objects", "path": path})
            start_line = start.get("line")
            start_column = start.get("column")
            end_line = end.get("line")
            end_column = end.get("column")
            if not isinstance(start_line, int) or start_line < 1 or not isinstance(start_column, int) or start_column < 0:
                raise ACPLiteError(-32602, "Invalid params", {"reason": "range.start must contain a positive line and non-negative column", "path": path})
            if not isinstance(end_line, int) or end_line < 1 or not isinstance(end_column, int) or end_column < 0:
                raise ACPLiteError(-32602, "Invalid params", {"reason": "range.end must contain a positive line and non-negative column", "path": path})
            start_offset = self._line_col_to_offset(line_starts, start_line, start_column)
            end_offset = self._line_col_to_offset(line_starts, end_line, end_column)
            if start_offset is None or end_offset is None or start_offset > len(current_text) or end_offset > len(current_text) or end_offset < start_offset:
                raise ACPLiteError(-32602, "Invalid params", {"reason": "text edit range is out of bounds", "path": path})
            new_text = edit.get("new_text")
            if not isinstance(new_text, str):
                raise ACPLiteError(-32602, "Invalid params", {"reason": "text edits must contain string new_text", "path": path})
            old_text = edit.get("old_text")
            if old_text is not None:
                if not isinstance(old_text, str):
                    raise ACPLiteError(-32602, "Invalid params", {"reason": "old_text must be a string when provided", "path": path})
                if current_text[start_offset:end_offset] != old_text:
                    raise ACPLiteError(-32602, "Invalid params", {"reason": "old_text does not match current document contents", "path": path})
            materialized.append({
                "start_offset": start_offset,
                "end_offset": end_offset,
                "new_text": new_text,
            })
        previous_end = -1
        for item in sorted(materialized, key=lambda value: (value["start_offset"], value["end_offset"])):
            if item["start_offset"] < previous_end:
                raise ACPLiteError(-32602, "Invalid params", {"reason": "text edit ranges must not overlap", "path": path})
            previous_end = item["end_offset"]
        updated = current_text
        for item in sorted(materialized, key=lambda value: value["start_offset"], reverse=True):
            updated = updated[:item["start_offset"]] + item["new_text"] + updated[item["end_offset"]:]
        return updated, len(materialized)

    def _normalize_workspace_edit_path(self, workspace_root: Path, change: dict[str, Any]) -> str:
        path = self._require_string(change, "path")
        resolved = self._resolve_workspace_path(workspace_root, path)
        return resolved.relative_to(workspace_root).as_posix()

    def _resolve_editor_path(self, thread_id: str, params: dict[str, Any]) -> str:
        path = params.get("path")
        if isinstance(path, str) and path.strip():
            return path.strip()
        state = self.editor_state.get_state(thread_id) or {}
        active_file = state.get("active_file")
        if isinstance(active_file, str) and active_file.strip():
            return active_file.strip()
        raise ACPLiteError(-32602, "Invalid params", {"reason": "path is required when no active editor file is available"})

    def _resolve_workspace_path(self, workspace_root: Path, path: str) -> Path:
        candidate = Path(path)
        resolved = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
        try:
            resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise ACPLiteError(-32602, "Invalid params", {"reason": "path must stay inside the workspace", "path": path}) from exc
        return resolved

    def _require_positive_int(self, params: dict[str, Any], key: str) -> int:
        value = params.get(key)
        if not isinstance(value, int) or value < 1:
            raise ACPLiteError(-32602, "Invalid params", {"reason": f"{key} must be a positive integer"})
        return value

    def _require_non_negative_int(self, params: dict[str, Any], key: str) -> int:
        value = params.get(key)
        if not isinstance(value, int) or value < 0:
            raise ACPLiteError(-32602, "Invalid params", {"reason": f"{key} must be a non-negative integer"})
        return value

    def _resolve_apply_mode(self, params: dict[str, Any]) -> str:
        value = params.get("apply_mode", "server")
        if not isinstance(value, str) or value not in {"server", "workspace_edit"}:
            raise ACPLiteError(-32602, "Invalid params", {"reason": "apply_mode must be either 'server' or 'workspace_edit'"})
        return value

    def _get_optional_position(self, params: dict[str, Any]) -> tuple[int, int] | None:
        line = params.get("line")
        column = params.get("column")
        if line is None and column is None:
            return None
        if line is None or column is None:
            raise ACPLiteError(-32602, "Invalid params", {"reason": "line and column must be provided together"})
        return self._require_positive_int(params, "line"), self._require_non_negative_int(params, "column")

    def _resolve_code_action_only_filters(self, params: dict[str, Any]) -> list[str]:
        context = params.get("context")
        if context is not None and not isinstance(context, dict):
            raise ACPLiteError(-32602, "Invalid params", {"reason": "context must be an object when provided"})
        normalized: list[str] = []
        for raw_value in [params.get("only"), None if context is None else context.get("only")]:
            if raw_value is None:
                continue
            values = [raw_value] if isinstance(raw_value, str) else raw_value
            if not isinstance(values, list):
                raise ACPLiteError(-32602, "Invalid params", {"reason": "code action filters must be a string or array of strings"})
            for item in values:
                if not isinstance(item, str) or not item.strip():
                    raise ACPLiteError(-32602, "Invalid params", {"reason": "code action filter entries must be non-empty strings"})
                value = item.strip()
                if value not in normalized:
                    normalized.append(value)
        return normalized

    def _resolve_code_action_context_diagnostics(self, params: dict[str, Any], path: str) -> list[dict[str, Any]] | None:
        context = params.get("context")
        if context is None:
            return None
        if not isinstance(context, dict):
            raise ACPLiteError(-32602, "Invalid params", {"reason": "context must be an object when provided"})
        if "diagnostics" not in context:
            return None
        return self._normalize_requested_diagnostics(context.get("diagnostics"), path, "context.diagnostics")

    def _action_kind_matches_filters(self, action_kind: Any, only_filters: list[str]) -> bool:
        if not only_filters:
            return True
        if not isinstance(action_kind, str) or not action_kind:
            return False
        for only_filter in only_filters:
            if action_kind == only_filter or action_kind.startswith(f"{only_filter}."):
                return True
        return False

    def _resolve_planning_diagnostics(
        self,
        thread_id: str,
        path: str,
        content: str,
        params: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        if "diagnostics" in params:
            return self._normalize_requested_diagnostics(params.get("diagnostics"), path, "diagnostics"), True
        diagnostics = self._merge_diagnostics(
            self.editor_state.get_diagnostics(thread_id, path=path),
            self._compute_diagnostics(path, content),
        )
        return diagnostics, False

    def _normalize_requested_diagnostics(self, value: Any, path: str, field_name: str) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ACPLiteError(-32602, "Invalid params", {"reason": f"{field_name} must be an array when provided"})
        diagnostics: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ACPLiteError(-32602, "Invalid params", {"reason": f"{field_name} entries must be objects"})
            diagnostic = dict(item)
            diagnostic_path = diagnostic.get("path")
            if diagnostic_path is None:
                diagnostic["path"] = path
            elif not isinstance(diagnostic_path, str):
                raise ACPLiteError(-32602, "Invalid params", {"reason": f"{field_name} path values must be strings"})
            elif diagnostic_path != path:
                continue
            diagnostics.append(diagnostic)
        return self._merge_diagnostics(diagnostics, [])

    def _filter_fixes_by_diagnostics(self, fixes: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not diagnostics:
            return []
        if not any(isinstance(diagnostic.get("line"), int) and diagnostic.get("line") >= 1 for diagnostic in diagnostics):
            return fixes
        return [
            fix for fix in fixes
            if any(self._fix_matches_diagnostic(fix, diagnostic) for diagnostic in diagnostics)
        ]

    def _fix_matches_diagnostic(self, fix: dict[str, Any], diagnostic: dict[str, Any]) -> bool:
        diagnostic_line = diagnostic.get("line")
        if not isinstance(diagnostic_line, int) or diagnostic_line < 1:
            return False
        start_line = fix.get("start_line", fix.get("line"))
        end_line = fix.get("end_line", fix.get("line", start_line))
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            return False
        if diagnostic_line < start_line or diagnostic_line > end_line:
            return False
        diagnostic_column = diagnostic.get("column")
        if not isinstance(diagnostic_column, int) or diagnostic_column < 0:
            return True
        start_column = fix.get("start_column", 0)
        end_column = fix.get("end_column", start_column)
        if not isinstance(start_column, int) or not isinstance(end_column, int):
            return True
        if start_line == end_line == diagnostic_line:
            if start_column == end_column:
                return diagnostic_column == start_column
            return start_column <= diagnostic_column <= end_column
        if diagnostic_line == start_line:
            return diagnostic_column >= start_column
        if diagnostic_line == end_line:
            return diagnostic_column <= end_column
        return True

    def _normalize_editor_action_request(self, thread_id: str, params: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
        action = params.get("action")
        if not isinstance(action, dict):
            raise ACPLiteError(-32602, "Invalid params", {"reason": "action must be an object"})
        command = action.get("command")
        arguments = action.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ACPLiteError(-32602, "Invalid params", {"reason": "action.arguments must be an object"})
        merged = dict(arguments)
        merged["thread_id"] = thread_id
        inputs = params.get("inputs") or {}
        if not isinstance(inputs, dict):
            raise ACPLiteError(-32602, "Invalid params", {"reason": "inputs must be an object"})
        merged.update(inputs)
        for key, value in params.items():
            if key in {"action", "inputs", "thread_id"}:
                continue
            merged[key] = value
        return action, command, merged

    async def _plan_document_format(self, thread_id: str, params: dict[str, Any]) -> dict[str, Any]:
        path, target_path, _workspace_root = self._resolve_editor_target(thread_id, params)
        if target_path.suffix.lower() != ".json":
            raise ACPLiteError(-32602, "Invalid params", {"reason": "formatDocument currently only supports .json files", "path": path})
        indent = self._resolve_format_indent(params)
        read_result = await self.sandbox.read_file(path, thread_id=thread_id)
        if not read_result.get("success"):
            raise ACPLiteError(-32004, read_result.get("error", "Failed to read file"), {"path": path})
        content = read_result.get("content", "")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ACPLiteError(-32000, "Document format unavailable", {
                "path": path,
                "reason": exc.msg,
                "line": exc.lineno,
                "column": max(exc.colno - 1, 0),
            }) from exc
        formatted = json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"
        return {
            "thread_id": thread_id,
            "path": path,
            "changed": formatted != content,
            "content": formatted,
            "old_size": len(content),
            "new_size": len(formatted),
        }

    async def _plan_remove_json_trailing_commas(self, thread_id: str, params: dict[str, Any]) -> dict[str, Any]:
        path, target_path, _workspace_root = self._resolve_editor_target(thread_id, params)
        if target_path.suffix.lower() != ".json":
            raise ACPLiteError(-32602, "Invalid params", {"reason": "removeJsonTrailingCommas currently only supports .json files", "path": path})
        read_result = await self.sandbox.read_file(path, thread_id=thread_id)
        if not read_result.get("success"):
            raise ACPLiteError(-32004, read_result.get("error", "Failed to read file"), {"path": path})
        content = read_result.get("content", "")
        diagnostics, has_requested_diagnostics = self._resolve_planning_diagnostics(thread_id, path, content, params)
        fixes = self._find_json_trailing_comma_fixes(content)
        if has_requested_diagnostics:
            fixes = self._filter_fixes_by_diagnostics(fixes, diagnostics)
        if not fixes:
            return {
                "thread_id": thread_id,
                "path": path,
                "changed": False,
                "content": content,
                "old_size": len(content),
                "new_size": len(content),
                "fixes": [],
                "diagnostic_count": len(diagnostics),
            }
        updated = self._apply_json_trailing_comma_fixes(content, fixes)
        try:
            json.loads(updated)
        except json.JSONDecodeError as exc:
            raise ACPLiteError(-32000, "JSON quick fix did not produce a valid document", {
                "path": path,
                "reason": exc.msg,
                "line": exc.lineno,
                "column": max(exc.colno - 1, 0),
            }) from exc
        return {
            "thread_id": thread_id,
            "path": path,
            "changed": updated != content,
            "content": updated,
            "old_size": len(content),
            "new_size": len(updated),
            "fixes": fixes,
            "diagnostic_count": len(diagnostics),
        }

    async def _plan_remove_json_comments(self, thread_id: str, params: dict[str, Any]) -> dict[str, Any]:
        path, target_path, _workspace_root = self._resolve_editor_target(thread_id, params)
        if target_path.suffix.lower() != ".json":
            raise ACPLiteError(-32602, "Invalid params", {"reason": "removeJsonComments currently only supports .json files", "path": path})
        read_result = await self.sandbox.read_file(path, thread_id=thread_id)
        if not read_result.get("success"):
            raise ACPLiteError(-32004, read_result.get("error", "Failed to read file"), {"path": path})
        content = read_result.get("content", "")
        diagnostics, has_requested_diagnostics = self._resolve_planning_diagnostics(thread_id, path, content, params)
        fixes = self._find_json_comment_fixes(content)
        if has_requested_diagnostics:
            fixes = self._filter_fixes_by_diagnostics(fixes, diagnostics)
        if not fixes:
            return {
                "thread_id": thread_id,
                "path": path,
                "changed": False,
                "content": content,
                "old_size": len(content),
                "new_size": len(content),
                "fixes": [],
                "diagnostic_count": len(diagnostics),
            }
        updated = self._apply_json_text_fixes(content, fixes)
        try:
            json.loads(updated)
        except json.JSONDecodeError as exc:
            raise ACPLiteError(-32000, "JSON quick fix did not produce a valid document", {
                "path": path,
                "reason": exc.msg,
                "line": exc.lineno,
                "column": max(exc.colno - 1, 0),
            }) from exc
        return {
            "thread_id": thread_id,
            "path": path,
            "changed": updated != content,
            "content": updated,
            "old_size": len(content),
            "new_size": len(updated),
            "fixes": fixes,
            "diagnostic_count": len(diagnostics),
        }

    async def _plan_remove_unused_python_imports(self, thread_id: str, params: dict[str, Any]) -> dict[str, Any]:
        path, target_path, _workspace_root = self._resolve_editor_target(thread_id, params)
        if target_path.suffix.lower() != ".py":
            raise ACPLiteError(-32602, "Invalid params", {"reason": "removeUnusedPythonImports currently only supports .py files", "path": path})
        read_result = await self.sandbox.read_file(path, thread_id=thread_id)
        if not read_result.get("success"):
            raise ACPLiteError(-32004, read_result.get("error", "Failed to read file"), {"path": path})
        content = read_result.get("content", "")
        diagnostics, has_requested_diagnostics = self._resolve_planning_diagnostics(thread_id, path, content, params)
        fixes = self._find_unused_python_import_fixes(content, diagnostics)
        if has_requested_diagnostics:
            fixes = self._filter_fixes_by_diagnostics(fixes, diagnostics)
        if not fixes:
            return {
                "thread_id": thread_id,
                "path": path,
                "changed": False,
                "content": content,
                "old_size": len(content),
                "new_size": len(content),
                "fixes": [],
                "diagnostic_count": len(diagnostics),
            }
        updated = self._apply_text_fixes(content, fixes)
        return {
            "thread_id": thread_id,
            "path": path,
            "changed": updated != content,
            "content": updated,
            "old_size": len(content),
            "new_size": len(updated),
            "fixes": fixes,
            "diagnostic_count": len(diagnostics),
        }

    async def _plan_insert_python_missing_colon(self, thread_id: str, params: dict[str, Any]) -> dict[str, Any]:
        path, target_path, _workspace_root = self._resolve_editor_target(thread_id, params)
        if target_path.suffix.lower() != ".py":
            raise ACPLiteError(-32602, "Invalid params", {"reason": "insertPythonMissingColon currently only supports .py files", "path": path})
        read_result = await self.sandbox.read_file(path, thread_id=thread_id)
        if not read_result.get("success"):
            raise ACPLiteError(-32004, read_result.get("error", "Failed to read file"), {"path": path})
        content = read_result.get("content", "")
        diagnostics, has_requested_diagnostics = self._resolve_planning_diagnostics(thread_id, path, content, params)
        fixes = self._find_python_missing_colon_fixes(content, diagnostics)
        if has_requested_diagnostics:
            fixes = self._filter_fixes_by_diagnostics(fixes, diagnostics)
        if not fixes:
            return {
                "thread_id": thread_id,
                "path": path,
                "changed": False,
                "content": content,
                "old_size": len(content),
                "new_size": len(content),
                "fixes": [],
                "diagnostic_count": len(diagnostics),
            }
        updated = self._apply_text_fixes(content, fixes)
        return {
            "thread_id": thread_id,
            "path": path,
            "changed": updated != content,
            "content": updated,
            "old_size": len(content),
            "new_size": len(updated),
            "fixes": fixes,
            "diagnostic_count": len(diagnostics),
        }

    async def _plan_insert_missing_semicolons(self, thread_id: str, params: dict[str, Any]) -> dict[str, Any]:
        path, target_path, _workspace_root = self._resolve_editor_target(thread_id, params)
        if not self._supports_missing_semicolon_actions(target_path):
            raise ACPLiteError(-32602, "Invalid params", {"reason": "insertMissingSemicolons currently only supports .js/.jsx/.ts/.tsx files", "path": path})
        read_result = await self.sandbox.read_file(path, thread_id=thread_id)
        if not read_result.get("success"):
            raise ACPLiteError(-32004, read_result.get("error", "Failed to read file"), {"path": path})
        content = read_result.get("content", "")
        diagnostics, has_requested_diagnostics = self._resolve_planning_diagnostics(thread_id, path, content, params)
        fixes = self._find_missing_semicolon_fixes(content, diagnostics)
        if has_requested_diagnostics:
            fixes = self._filter_fixes_by_diagnostics(fixes, diagnostics)
        if not fixes:
            return {
                "thread_id": thread_id,
                "path": path,
                "changed": False,
                "content": content,
                "old_size": len(content),
                "new_size": len(content),
                "fixes": [],
                "diagnostic_count": len(diagnostics),
            }
        updated = self._apply_text_fixes(content, fixes)
        return {
            "thread_id": thread_id,
            "path": path,
            "changed": updated != content,
            "content": updated,
            "old_size": len(content),
            "new_size": len(updated),
            "fixes": fixes,
            "diagnostic_count": len(diagnostics),
        }

    def _resolve_format_indent(self, params: dict[str, Any]) -> int:
        value = params.get("indent", 2)
        if not isinstance(value, int) or value < 1 or value > 8:
            raise ACPLiteError(-32602, "Invalid params", {"reason": "indent must be an integer between 1 and 8"})
        return value

    def _build_symbol_workspace_edit(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "document_changes": [
                {
                    "path": item["path"],
                    "kind": "textEdits",
                    "edits": [
                        {
                            "range": {
                                "start": {"line": edit["line"], "column": edit["start_column"]},
                                "end": {"line": edit["line"], "column": edit["end_column"]},
                            },
                            "old_text": edit["old_text"],
                            "new_text": edit["new_text"],
                        }
                        for edit in item["edits"]
                    ],
                }
                for item in plan["planned_files"]
            ],
            "change_count": plan["occurrences_changed"],
        }

    def _build_document_format_workspace_edit(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not plan["changed"]:
            return {"document_changes": [], "change_count": 0}
        return {
            "document_changes": [{
                "path": plan["path"],
                "kind": "replaceDocument",
                "content": plan["content"],
            }],
            "change_count": 1,
        }

    def _build_json_trailing_comma_workspace_edit(self, plan: dict[str, Any]) -> dict[str, Any]:
        return self._build_text_fixes_workspace_edit(plan["path"], plan["fixes"])

    def _build_json_comment_workspace_edit(self, plan: dict[str, Any]) -> dict[str, Any]:
        return self._build_text_fixes_workspace_edit(plan["path"], plan["fixes"])

    def _build_unused_python_import_workspace_edit(self, plan: dict[str, Any]) -> dict[str, Any]:
        return self._build_text_fixes_workspace_edit(plan["path"], plan["fixes"])

    def _build_python_missing_colon_workspace_edit(self, plan: dict[str, Any]) -> dict[str, Any]:
        return self._build_text_fixes_workspace_edit(plan["path"], plan["fixes"])

    def _build_missing_semicolon_workspace_edit(self, plan: dict[str, Any]) -> dict[str, Any]:
        return self._build_text_fixes_workspace_edit(plan["path"], plan["fixes"])

    def _build_text_fixes_workspace_edit(self, path: str, fixes: list[dict[str, Any]]) -> dict[str, Any]:
        if not fixes:
            return {"document_changes": [], "change_count": 0}
        return {
            "document_changes": [{
                "path": path,
                "kind": "textEdits",
                "edits": [{
                    "range": {
                        "start": {
                            "line": fix.get("start_line", fix.get("line")),
                            "column": fix["start_column"],
                        },
                        "end": {
                            "line": fix.get("end_line", fix.get("line")),
                            "column": fix["end_column"],
                        },
                    },
                    "old_text": fix["old_text"],
                    "new_text": fix["new_text"],
                } for fix in fixes],
            }],
            "change_count": len(fixes),
        }

    async def _plan_symbol_rename(self, thread_id: str, params: dict[str, Any], new_name: str) -> dict[str, Any]:
        path, target_path, workspace_root, old_name, occurrences = self._inspect_editor_symbol(thread_id, params)
        if not old_name:
            raise ACPLiteError(-32004, "No symbol found at position", {"path": path, "line": params.get("line"), "column": params.get("column")})
        self._validate_new_symbol_name(new_name, target_path.suffix.lower())
        if old_name == new_name:
            return {
                "thread_id": thread_id,
                "path": path,
                "symbol": old_name,
                "new_name": new_name,
                "files_changed": 0,
                "occurrences_changed": 0,
                "planned_files": [],
            }

        grouped: dict[Path, list[dict[str, int]]] = defaultdict(list)
        for occurrence in occurrences:
            grouped[occurrence["path"]].append(occurrence)

        planned_files: list[dict[str, Any]] = []
        total_changes = 0
        for abs_path, file_occurrences in grouped.items():
            relative_path = abs_path.relative_to(workspace_root).as_posix()
            read_result = await self.sandbox.read_file(relative_path, thread_id=thread_id)
            if not read_result.get("success"):
                raise ACPLiteError(-32004, read_result.get("error", "Failed to read file"), {"path": relative_path})
            source = read_result.get("content", "")
            replacements = self._build_symbol_replacements(source, file_occurrences, old_name)
            if not replacements:
                continue
            updated_content = self._apply_symbol_replacements(source, replacements, new_name)
            planned_files.append({
                "path": relative_path,
                "occurrences": len(replacements),
                "size": len(updated_content),
                "content": updated_content,
                "edits": [
                    {
                        "line": item["line"],
                        "start_column": item["start_column"],
                        "end_column": item["end_column"],
                        "old_text": old_name,
                        "new_text": new_name,
                        "text": item["text"],
                    }
                    for item in replacements
                ],
            })
            total_changes += len(replacements)

        if total_changes == 0:
            raise ACPLiteError(-32000, "Rename produced no edits", {"symbol": old_name, "new_name": new_name})

        return {
            "thread_id": thread_id,
            "path": path,
            "symbol": old_name,
            "new_name": new_name,
            "files_changed": len(planned_files),
            "occurrences_changed": total_changes,
            "planned_files": planned_files,
        }

    def _resolve_editor_target(self, thread_id: str, params: dict[str, Any]) -> tuple[str, Path, Path]:
        path = self._resolve_editor_path(thread_id, params)
        workspace_root = Path(self.sandbox.get_workspace_dir(thread_id)).resolve()
        target_path = self._resolve_workspace_path(workspace_root, path)
        if not target_path.is_file():
            raise ACPLiteError(-32004, "File not found", {"path": path})
        return path, target_path, workspace_root

    def _inspect_editor_symbol(self, thread_id: str, params: dict[str, Any]) -> tuple[str, Path, Path, str | None, list[dict[str, Any]]]:
        line = self._require_positive_int(params, "line")
        column = self._require_non_negative_int(params, "column")
        path, target_path, workspace_root = self._resolve_editor_target(thread_id, params)
        symbol, occurrences = self._collect_symbol_occurrences(target_path, line, column, workspace_root)
        return path, target_path, workspace_root, symbol, occurrences

    def _supports_symbol_actions(self, file_path: Path) -> bool:
        suffix = file_path.suffix.lower()
        return suffix == ".py" or static_code_intel.supports_extension(suffix)

    def _supports_missing_semicolon_actions(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}

    def _collect_symbol_occurrences(self, file_path: Path, line: int, column: int, workspace_root: Path) -> tuple[str | None, list[dict[str, Any]]]:
        suffix = file_path.suffix.lower()
        if suffix == ".py":
            return self._collect_python_symbol_occurrences(file_path, line, column, workspace_root)
        if static_code_intel.supports_extension(suffix):
            return self._collect_static_symbol_occurrences(file_path, line, column, workspace_root)
        raise ACPLiteError(-32602, "Invalid params", {"reason": f"rename is not supported for '{suffix or 'unknown'}' files"})

    def _collect_python_symbol_occurrences(self, file_path: Path, line: int, column: int, workspace_root: Path) -> tuple[str | None, list[dict[str, Any]]]:
        try:
            import jedi
        except Exception as exc:
            raise ACPLiteError(-32000, "Python rename support is unavailable", {"detail": str(exc)}) from exc

        source = file_path.read_text(encoding="utf-8", errors="replace")
        symbol = self._symbol_at_position(source, line, column)
        if not symbol:
            return None, []

        script = jedi.Script(code=source, path=str(file_path))
        references = script.get_references(line=line, column=column)
        occurrences: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int]] = set()
        for ref in references:
            module_path = getattr(ref, "module_path", None)
            if not module_path:
                continue
            ref_path = Path(str(module_path)).resolve()
            try:
                ref_path.relative_to(workspace_root)
            except ValueError:
                continue
            key = (str(ref_path), int(ref.line), int(ref.column))
            if key in seen:
                continue
            seen.add(key)
            occurrences.append({"path": ref_path, "line": int(ref.line), "column": int(ref.column)})
        return symbol, sorted(occurrences, key=lambda item: (str(item["path"]), item["line"], item["column"]))

    def _collect_static_symbol_occurrences(self, file_path: Path, line: int, column: int, workspace_root: Path) -> tuple[str | None, list[dict[str, Any]]]:
        symbol, target = static_code_intel.resolve_symbol(str(file_path), line, column)
        if not symbol or target is None:
            return symbol, []
        references = static_code_intel.find_references_for_symbol(target, str(file_path))
        occurrences: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int]] = set()
        for ref in references:
            ref_path = Path(ref.file_path).resolve()
            try:
                ref_path.relative_to(workspace_root)
            except ValueError:
                continue
            key = (str(ref_path), int(ref.line), int(ref.column))
            if key in seen:
                continue
            seen.add(key)
            occurrences.append({"path": ref_path, "line": int(ref.line), "column": int(ref.column)})
        return symbol, sorted(occurrences, key=lambda item: (str(item["path"]), item["line"], item["column"]))

    def _apply_symbol_rename(self, source: str, occurrences: list[dict[str, int]], old_name: str, new_name: str) -> tuple[str, int]:
        replacements = self._build_symbol_replacements(source, occurrences, old_name)
        if not replacements:
            return source, 0
        return self._apply_symbol_replacements(source, replacements, new_name), len(replacements)

    def _build_symbol_replacements(self, source: str, occurrences: list[dict[str, int]], old_name: str) -> list[dict[str, Any]]:
        line_starts = self._build_line_starts(source)
        lines = source.splitlines()
        replacements: list[dict[str, Any]] = []
        seen_offsets: set[int] = set()
        for occurrence in occurrences:
            offset = self._line_col_to_offset(line_starts, occurrence["line"], occurrence["column"])
            if offset is None or offset in seen_offsets:
                continue
            if source[offset:offset + len(old_name)] != old_name:
                continue
            if not self._is_identifier_boundary(source, offset - 1) or not self._is_identifier_boundary(source, offset + len(old_name)):
                continue
            seen_offsets.add(offset)
            line = occurrence["line"]
            replacements.append({
                "start_offset": offset,
                "end_offset": offset + len(old_name),
                "line": line,
                "start_column": occurrence["column"],
                "end_column": occurrence["column"] + len(old_name),
                "text": lines[line - 1].strip() if 0 < line <= len(lines) else "",
            })
        return sorted(replacements, key=lambda item: item["start_offset"])

    def _apply_symbol_replacements(self, source: str, replacements: list[dict[str, Any]], new_name: str) -> str:
        updated = source
        for replacement in sorted(replacements, key=lambda item: item["start_offset"], reverse=True):
            updated = updated[:replacement["start_offset"]] + new_name + updated[replacement["end_offset"]:]
        return updated

    def _find_json_trailing_comma_fixes(self, source: str) -> list[dict[str, Any]]:
        line_starts = self._build_line_starts(source)
        lines = source.splitlines()
        fixes: list[dict[str, Any]] = []
        in_string = False
        escaping = False
        for index, char in enumerate(source):
            if in_string:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char != ",":
                continue
            lookahead = index + 1
            while lookahead < len(source) and source[lookahead].isspace():
                lookahead += 1
            if lookahead >= len(source) or source[lookahead] not in "]}":
                continue
            line, column = self._offset_to_line_col(line_starts, index)
            fixes.append({
                "start_offset": index,
                "end_offset": index + 1,
                "start_line": line,
                "end_line": line,
                "line": line,
                "start_column": column,
                "end_column": column + 1,
                "old_text": ",",
                "new_text": "",
                "text": lines[line - 1].strip() if 0 < line <= len(lines) else "",
            })
        return fixes

    def _apply_json_trailing_comma_fixes(self, source: str, fixes: list[dict[str, Any]]) -> str:
        return self._apply_text_fixes(source, fixes)

    def _find_json_comment_fixes(self, source: str) -> list[dict[str, Any]]:
        line_starts = self._build_line_starts(source)
        lines = source.splitlines()
        fixes: list[dict[str, Any]] = []
        in_string = False
        escaping = False
        index = 0
        while index < len(source):
            char = source[index]
            if in_string:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == '"':
                    in_string = False
                index += 1
                continue
            if char == '"':
                in_string = True
                index += 1
                continue
            if char == "/" and index + 1 < len(source):
                next_char = source[index + 1]
                if next_char == "/":
                    end_offset = index + 2
                    while end_offset < len(source) and source[end_offset] not in "\r\n":
                        end_offset += 1
                    start_line, start_column = self._offset_to_line_col(line_starts, index)
                    end_line, end_column = self._offset_to_line_col(line_starts, end_offset)
                    fixes.append({
                        "start_offset": index,
                        "end_offset": end_offset,
                        "start_line": start_line,
                        "end_line": end_line,
                        "start_column": start_column,
                        "end_column": end_column,
                        "old_text": source[index:end_offset],
                        "new_text": "",
                        "text": lines[start_line - 1].strip() if 0 < start_line <= len(lines) else "",
                    })
                    index = end_offset
                    continue
                if next_char == "*":
                    end_offset = index + 2
                    while end_offset + 1 < len(source) and source[end_offset:end_offset + 2] != "*/":
                        end_offset += 1
                    if end_offset + 1 >= len(source):
                        break
                    end_offset += 2
                    start_line, start_column = self._offset_to_line_col(line_starts, index)
                    end_line, end_column = self._offset_to_line_col(line_starts, end_offset)
                    fixes.append({
                        "start_offset": index,
                        "end_offset": end_offset,
                        "start_line": start_line,
                        "end_line": end_line,
                        "start_column": start_column,
                        "end_column": end_column,
                        "old_text": source[index:end_offset],
                        "new_text": "",
                        "text": lines[start_line - 1].strip() if 0 < start_line <= len(lines) else "",
                    })
                    index = end_offset
                    continue
            index += 1
        return fixes

    def _apply_json_text_fixes(self, source: str, fixes: list[dict[str, Any]]) -> str:
        updated = source
        for fix in sorted(fixes, key=lambda item: item["start_offset"], reverse=True):
            updated = updated[:fix["start_offset"]] + fix["new_text"] + updated[fix["end_offset"]:]
        return updated

    def _find_unused_python_import_fixes(self, source: str, diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            module = ast.parse(source)
        except SyntaxError:
            return []
        lines = source.splitlines(keepends=True)
        line_starts = self._build_line_starts(source)
        import_nodes = sorted(
            [node for node in ast.walk(module) if isinstance(node, (ast.Import, ast.ImportFrom))],
            key=lambda node: (node.lineno, node.col_offset),
        )
        grouped: dict[tuple[int, int, int], dict[str, Any]] = {}
        for diagnostic in diagnostics:
            if not self._is_unused_python_import_diagnostic(diagnostic):
                continue
            line = diagnostic.get("line")
            if not isinstance(line, int) or line < 1:
                continue
            unused_name = self._extract_unused_python_import_name(str(diagnostic.get("message") or ""))
            for node in import_nodes:
                end_line = getattr(node, "end_lineno", node.lineno)
                if node.lineno > line or end_line < line:
                    continue
                alias_indexes = self._match_unused_python_import_aliases(node, unused_name)
                if not alias_indexes:
                    continue
                key = (node.lineno, node.col_offset, getattr(node, "end_col_offset", node.col_offset))
                entry = grouped.setdefault(key, {"node": node, "alias_indexes": set()})
                entry["alias_indexes"].update(alias_indexes)
                break
        fixes: list[dict[str, Any]] = []
        for entry in sorted(grouped.values(), key=lambda item: (item["node"].lineno, item["node"].col_offset)):
            fix = self._build_unused_python_import_fix(source, lines, line_starts, entry["node"], sorted(entry["alias_indexes"]))
            if fix is not None:
                fixes.append(fix)
        return fixes

    def _is_unused_python_import_diagnostic(self, diagnostic: dict[str, Any]) -> bool:
        code = str(diagnostic.get("code") or "").strip().upper()
        message = str(diagnostic.get("message") or "").strip().lower()
        if code == "F401":
            return True
        return "imported but unused" in message or "import is not accessed" in message or message.startswith("unused import")

    def _extract_unused_python_import_name(self, message: str) -> str | None:
        patterns = [
            r"[`'\"]([^`'\"]+)[`'\"]\s+imported\s+but\s+unused",
            r"import\s+[`'\"]([^`'\"]+)[`'\"]\s+is\s+not\s+accessed",
            r"unused\s+import[:\s]+[`'\"]?([A-Za-z_][\w\.]*)[`'\"]?",
        ]
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _match_unused_python_import_aliases(self, node: ast.Import | ast.ImportFrom, unused_name: str | None) -> list[int]:
        if unused_name is None:
            return [0] if len(node.names) == 1 else []
        normalized = unused_name.strip().lower()
        matches: list[int] = []
        for index, alias in enumerate(node.names):
            if self._unused_python_import_name_matches(alias, node, normalized):
                matches.append(index)
        return matches

    def _unused_python_import_name_matches(self, alias: ast.alias, node: ast.Import | ast.ImportFrom, unused_name: str) -> bool:
        candidates = {
            alias.name,
            alias.name.split(".")[-1],
        }
        if alias.asname:
            candidates.add(alias.asname)
        if isinstance(node, ast.ImportFrom) and node.module:
            candidates.add(f"{node.module}.{alias.name}")
            candidates.add(f"{node.module}.{alias.name.split('.')[-1]}")
        return any(candidate.lower() == unused_name for candidate in candidates)

    def _build_unused_python_import_fix(
        self,
        source: str,
        lines: list[str],
        line_starts: list[int],
        node: ast.Import | ast.ImportFrom,
        alias_indexes: list[int],
    ) -> dict[str, Any] | None:
        if not alias_indexes:
            return None
        if node.lineno != getattr(node, "end_lineno", node.lineno) or node.lineno < 1 or node.lineno > len(lines):
            return None
        raw_line = lines[node.lineno - 1]
        line_body = raw_line.rstrip("\r\n")
        if not line_body:
            return None
        indent_width = len(line_body) - len(line_body.lstrip(" \t"))
        if node.col_offset != indent_width:
            return None
        valid_indexes = sorted({index for index in alias_indexes if 0 <= index < len(node.names)})
        if not valid_indexes:
            return None
        line_start_offset = line_starts[node.lineno - 1]
        if len(valid_indexes) >= len(node.names):
            end_offset = line_start_offset + len(raw_line)
            end_line, end_column = self._offset_to_line_col(line_starts, end_offset)
            return {
                "start_offset": line_start_offset,
                "end_offset": end_offset,
                "start_line": node.lineno,
                "end_line": end_line,
                "line": node.lineno,
                "start_column": 0,
                "end_column": end_column,
                "old_text": raw_line,
                "new_text": "",
                "text": line_body.strip(),
            }
        kept_aliases = [alias for index, alias in enumerate(node.names) if index not in set(valid_indexes)]
        statement_end = min(getattr(node, "end_col_offset", len(line_body)), len(line_body))
        trailing_suffix = line_body[statement_end:]
        replacement = (" " * indent_width) + self._render_python_import_statement(node, kept_aliases) + trailing_suffix
        end_offset = line_start_offset + len(line_body)
        return {
            "start_offset": line_start_offset,
            "end_offset": end_offset,
            "start_line": node.lineno,
            "end_line": node.lineno,
            "line": node.lineno,
            "start_column": 0,
            "end_column": len(line_body),
            "old_text": line_body,
            "new_text": replacement,
            "text": line_body.strip(),
        }

    def _render_python_import_statement(self, node: ast.Import | ast.ImportFrom, aliases: list[ast.alias]) -> str:
        rendered_aliases = ", ".join(
            alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"
            for alias in aliases
        )
        if isinstance(node, ast.Import):
            return f"import {rendered_aliases}"
        dots = "." * node.level
        module = node.module or ""
        return f"from {dots}{module} import {rendered_aliases}"

    def _find_python_missing_colon_fixes(self, source: str, diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        lines = source.splitlines(keepends=True)
        line_starts = self._build_line_starts(source)
        fixes: list[dict[str, Any]] = []
        seen_lines: set[int] = set()
        for diagnostic in diagnostics:
            message = str(diagnostic.get("message") or "")
            line = diagnostic.get("line")
            if "expected ':'" not in message or not isinstance(line, int) or line < 1 or line > len(lines) or line in seen_lines:
                continue
            seen_lines.add(line)
            fix = self._build_python_missing_colon_fix(lines[line - 1], line, line_starts)
            if fix is not None:
                fixes.append(fix)
        return fixes

    def _find_missing_semicolon_fixes(self, source: str, diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        lines = source.splitlines(keepends=True)
        line_starts = self._build_line_starts(source)
        fixes: list[dict[str, Any]] = []
        seen_lines: set[int] = set()
        for diagnostic in diagnostics:
            message = str(diagnostic.get("message") or "")
            line = diagnostic.get("line")
            if not self._is_missing_semicolon_diagnostic(message) or not isinstance(line, int) or line < 1 or line > len(lines) or line in seen_lines:
                continue
            seen_lines.add(line)
            fix = self._build_missing_semicolon_fix(lines[line - 1], line, line_starts)
            if fix is not None:
                fixes.append(fix)
        return fixes

    def _is_missing_semicolon_diagnostic(self, message: str) -> bool:
        normalized = message.strip().lower()
        return normalized in {"missing semicolon", "missing semicolon.", "expected ';'", "';' expected."}

    def _build_missing_semicolon_fix(self, raw_line: str, line_number: int, line_starts: list[int]) -> dict[str, Any] | None:
        content = raw_line.rstrip("\r\n")
        stripped = content.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
            return None
        comment_start = self._find_js_comment_start(content)
        code_segment = content if comment_start is None else content[:comment_start]
        trimmed_code = code_segment.rstrip(" \t")
        if not trimmed_code or trimmed_code.endswith(";"):
            return None
        insert_column = len(trimmed_code)
        start_offset = self._line_col_to_offset(line_starts, line_number, insert_column)
        if start_offset is None:
            return None
        return {
            "start_offset": start_offset,
            "end_offset": start_offset,
            "start_line": line_number,
            "end_line": line_number,
            "line": line_number,
            "start_column": insert_column,
            "end_column": insert_column,
            "old_text": "",
            "new_text": ";",
            "text": content.strip(),
        }

    def _find_js_comment_start(self, content: str) -> int | None:
        in_single = False
        in_double = False
        in_template = False
        escaping = False
        index = 0
        while index < len(content) - 1:
            char = content[index]
            next_char = content[index + 1]
            if in_single:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == "'":
                    in_single = False
                index += 1
                continue
            if in_double:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == '"':
                    in_double = False
                index += 1
                continue
            if in_template:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == "`":
                    in_template = False
                index += 1
                continue
            if char == "'":
                in_single = True
            elif char == '"':
                in_double = True
            elif char == "`":
                in_template = True
            elif char == "/" and next_char in {"/", "*"}:
                return index
            index += 1
        return None

    def _build_python_missing_colon_fix(self, raw_line: str, line_number: int, line_starts: list[int]) -> dict[str, Any] | None:
        content = raw_line.rstrip("\r\n")
        stripped = content.lstrip()
        if not stripped or stripped.startswith("#") or stripped.rstrip().endswith(":"):
            return None
        if not self._looks_like_python_block_header(stripped):
            return None
        insert_column = self._find_python_colon_insert_column(content)
        start_offset = self._line_col_to_offset(line_starts, line_number, insert_column)
        if start_offset is None:
            return None
        return {
            "start_offset": start_offset,
            "end_offset": start_offset,
            "start_line": line_number,
            "end_line": line_number,
            "line": line_number,
            "start_column": insert_column,
            "end_column": insert_column,
            "old_text": "",
            "new_text": ":",
            "text": content.strip(),
        }

    def _looks_like_python_block_header(self, stripped_line: str) -> bool:
        patterns = [
            r"if\b.+",
            r"elif\b.+",
            r"else\b.*",
            r"for\b.+",
            r"while\b.+",
            r"try\b.*",
            r"except\b.*",
            r"finally\b.*",
            r"with\b.+",
            r"def\b.+",
            r"class\b.+",
            r"match\b.+",
            r"case\b.+",
            r"async\s+def\b.+",
            r"async\s+for\b.+",
            r"async\s+with\b.+",
        ]
        return any(re.fullmatch(pattern, stripped_line.rstrip()) for pattern in patterns)

    def _find_python_colon_insert_column(self, content: str) -> int:
        try:
            for token in tokenize.generate_tokens(io.StringIO(content + "\n").readline):
                if token.type == tokenize.COMMENT:
                    return len(content[:token.start[1]].rstrip(" \t"))
        except (tokenize.TokenError, IndentationError):
            pass
        return len(content.rstrip(" \t"))

    def _apply_text_fixes(self, source: str, fixes: list[dict[str, Any]]) -> str:
        return self._apply_json_text_fixes(source, fixes)

    def _validate_new_symbol_name(self, name: str, suffix: str):
        pattern = r"[A-Za-z_]\w*" if suffix == ".py" else r"[A-Za-z_$][\w$]*"
        if not re.fullmatch(pattern, name):
            raise ACPLiteError(-32602, "Invalid params", {"reason": f"new_name is not a valid identifier for '{suffix or 'unknown'}' files"})

    def _serialize_references(self, occurrences: list[dict[str, Any]], workspace_root: Path) -> list[dict[str, Any]]:
        rendered: list[dict[str, Any]] = []
        line_cache: dict[Path, list[str]] = {}
        for occurrence in occurrences:
            abs_path = occurrence["path"]
            lines = line_cache.get(abs_path)
            if lines is None:
                lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
                line_cache[abs_path] = lines
            line = occurrence["line"]
            rendered.append({
                "path": abs_path.relative_to(workspace_root).as_posix(),
                "line": line,
                "column": occurrence["column"],
                "text": lines[line - 1].strip() if 0 < line <= len(lines) else "",
            })
        return rendered

    def _symbol_at_position(self, source: str, line: int, column: int) -> str | None:
        match = self._symbol_match_at_position(source, line, column)
        return None if match is None else match[0]

    def _symbol_match_at_position(self, source: str, line: int, column: int) -> tuple[str, int, int] | None:
        lines = source.splitlines()
        if line < 1 or line > len(lines):
            return None
        for match in re.finditer(r"[A-Za-z_$][\w$]*", lines[line - 1]):
            if match.start() <= column <= match.end():
                return match.group(0), match.start(), match.end()
        return None

    def _build_line_starts(self, source: str) -> list[int]:
        starts = [0]
        for index, char in enumerate(source):
            if char == "\n":
                starts.append(index + 1)
        return starts

    def _line_col_to_offset(self, line_starts: list[int], line: int, column: int) -> int | None:
        if line < 1 or line > len(line_starts):
            return None
        return line_starts[line - 1] + column

    def _offset_to_line_col(self, line_starts: list[int], offset: int) -> tuple[int, int]:
        line_index = bisect_right(line_starts, offset) - 1
        line = max(line_index + 1, 1)
        return line, offset - line_starts[line_index]

    def _is_identifier_boundary(self, source: str, index: int) -> bool:
        if index < 0 or index >= len(source):
            return True
        return not bool(re.match(r"[A-Za-z0-9_$]", source[index]))

    def _compute_diagnostics(self, path: str, content: str) -> list[dict[str, Any]]:
        suffix = Path(path).suffix.lower()
        if suffix == ".py":
            try:
                compile(content, path, "exec")
                return []
            except SyntaxError as exc:
                return [{
                    "severity": "error",
                    "message": exc.msg,
                    "line": exc.lineno or 1,
                    "column": max((exc.offset or 1) - 1, 0),
                    "source": "python-compile",
                }]
        if suffix == ".json":
            try:
                json.loads(content)
                return []
            except json.JSONDecodeError as exc:
                return [{
                    "severity": "error",
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": max(exc.colno - 1, 0),
                    "source": "json",
                }]
        return []

    def _merge_diagnostics(self, primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[Any, ...]] = set()
        merged: list[dict[str, Any]] = []
        for diagnostic in [*primary, *secondary]:
            key = (
                diagnostic.get("path"),
                diagnostic.get("severity"),
                diagnostic.get("message"),
                diagnostic.get("line"),
                diagnostic.get("column"),
                diagnostic.get("source"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(diagnostic)
        return merged

    def _handle_editor_state_change(self, event_type: str, thread_id: str, state: dict[str, Any] | None):
        diagnostics = [] if state is None else list(state.get("diagnostics") or [])
        for subscription_id, subscription in list(self._editor_subscriptions.items()):
            subscribed_thread = subscription.get("thread_id")
            if subscribed_thread and subscribed_thread != thread_id:
                continue
            events = set(subscription.get("events") or [])
            if "editorStateChanged" in events:
                self._queue_notification({
                    "jsonrpc": "2.0",
                    "method": "notifications/editorStateChanged",
                    "params": {
                        "subscription_id": subscription_id,
                        "thread_id": thread_id,
                        "event": event_type,
                        "editor_state": state,
                    },
                })
            if "editorDiagnosticsChanged" in events:
                self._queue_notification({
                    "jsonrpc": "2.0",
                    "method": "notifications/editorDiagnosticsChanged",
                    "params": {
                        "subscription_id": subscription_id,
                        "thread_id": thread_id,
                        "event": event_type,
                        "diagnostics": diagnostics,
                    },
                })

    def _queue_notification(self, payload: dict[str, Any]):
        self._pending_notifications.append(payload)
        if self._loop is not None and self._notification_event is not None:
            self._loop.call_soon_threadsafe(self._notification_event.set)

    def _drain_notifications(self) -> list[dict[str, Any]]:
        notifications = list(self._pending_notifications)
        self._pending_notifications.clear()
        return notifications

    def _error_response(self, req_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
        payload = {"code": code, "message": message}
        if data is not None:
            payload["data"] = data
        return {"jsonrpc": "2.0", "id": req_id, "error": payload}


async def _amain():
    server = ACPLiteServer()
    await server.serve_forever()


def main() -> int:
    asyncio.run(_amain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
