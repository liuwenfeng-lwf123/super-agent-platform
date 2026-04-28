import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path

from app.acp_lite import ACPLiteServer
from app.agents.store import ThreadStore
from app.local.editor_state import EditorStateStore


class FakeSandbox:
    def __init__(self, root: str):
        self.root = Path(root)
        self._history: dict[str, list[dict]] = {}

    def _thread_root(self, thread_id: str) -> Path:
        return self.root / thread_id

    def get_workspace_dir(self, thread_id: str) -> str:
        path = self._thread_root(thread_id) / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def get_outputs_dir(self, thread_id: str) -> str:
        path = self._thread_root(thread_id) / "outputs"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def get_uploads_dir(self, thread_id: str) -> str:
        path = self._thread_root(thread_id) / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def get_thread_runtime(self, thread_id: str) -> dict:
        return {
            "thread_id": thread_id,
            "backend": "fake-sandbox",
            "assigned": False,
            "default_backend": "fake-sandbox",
        }

    async def list_files(self, path: str = ".", thread_id: str = "") -> dict:
        base = Path(self.get_workspace_dir(thread_id)) / path
        if not base.exists():
            return {"success": False, "error": "File not found"}
        entries = []
        for entry in base.iterdir():
            entries.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else 0,
            })
        entries.sort(key=lambda item: (not item["is_dir"], item["name"]))
        return {"success": True, "path": path, "entries": entries}

    async def read_file(self, path: str, thread_id: str) -> dict:
        target = Path(self.get_workspace_dir(thread_id)) / path
        if not target.exists():
            return {"success": False, "error": "File not found"}
        return {"success": True, "content": target.read_text(encoding="utf-8"), "path": str(target)}

    async def write_file(self, path: str, content: str, thread_id: str) -> dict:
        target = Path(self.get_workspace_dir(thread_id)) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        old_content = target.read_text(encoding="utf-8") if target.exists() else None
        target.write_text(content, encoding="utf-8")
        self._history.setdefault(thread_id, []).append({
            "path": path,
            "action": "modify" if old_content is not None else "create",
            "new_size": len(content),
        })
        return {"success": True, "path": str(target), "size": len(content)}

    def get_file_history(self, thread_id: str, path: str | None = None, limit: int = 50) -> list[dict]:
        entries = list(self._history.get(thread_id, []))
        if path is not None:
            entries = [entry for entry in entries if entry.get("path") == path]
        return entries[-limit:]


class FakeClient:
    def __init__(self, client_id: str = "client-1"):
        self.client_id = client_id
        self.info = {"hostname": "tester", "os": "Darwin"}
        self.calls: list[tuple[str, dict]] = []

    async def send_request(self, action: str, params: dict, timeout: float = 120) -> dict:
        self.calls.append((action, dict(params)))
        if action == "get_system_info":
            return {"success": True, "info": "{\"hostname\": \"tester\"}"}
        return {"success": False, "error": f"unsupported action: {action}"}


class FakeGateway:
    def __init__(self, client: FakeClient | None = None):
        self.client = client
        self.bound: dict[str, str] = {}

    def list_clients(self) -> list[dict]:
        if self.client is None:
            return []
        return [{"client_id": self.client.client_id, "info": dict(self.client.info)}]

    def get_client(self, client_id: str):
        if self.client and self.client.client_id == client_id:
            return self.client
        return None

    def bind_thread(self, thread_id: str, client_id: str):
        self.bound[thread_id] = client_id

    def unbind_thread(self, thread_id: str):
        self.bound.pop(thread_id, None)

    def get_client_for_thread(self, thread_id: str):
        if self.client is None:
            return None
        bound_id = self.bound.get(thread_id)
        if bound_id == self.client.client_id:
            return self.client
        return None


class TestACPLiteServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ThreadStore(storage_path=str(Path(self.tmp.name) / "threads"))
        self.sandbox = FakeSandbox(str(Path(self.tmp.name) / "workspace-data"))
        self.client = FakeClient()
        self.gateway = FakeGateway(self.client)
        self.editor_state = EditorStateStore()
        self.server = ACPLiteServer(store=self.store, sandbox=self.sandbox, gateway=self.gateway, editor_state=self.editor_state)
        self.thread = asyncio.run(self.store.create(title="ACP Thread"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_initialize_returns_capabilities(self):
        response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "tester"}},
        }))
        self.assertEqual(response["result"]["protocolVersion"], "acp-lite-0.1")
        self.assertTrue(response["result"]["capabilities"]["workspace"]["applyEdit"])
        self.assertTrue(response["result"]["capabilities"]["workspace"]["workspaceEditPayload"])
        self.assertTrue(response["result"]["capabilities"]["editor"]["subscribe"])
        self.assertTrue(response["result"]["capabilities"]["editor"]["prepareRename"])
        self.assertEqual(response["result"]["capabilities"]["editor"]["insertMissingSemicolons"], ["js", "jsx", "ts", "tsx"])
        self.assertTrue(response["result"]["capabilities"]["editor"]["removeUnusedPythonImports"])
        self.assertTrue(response["result"]["capabilities"]["editor"]["insertPythonMissingColon"])
        self.assertTrue(response["result"]["capabilities"]["editor"]["removeJsonComments"])
        self.assertTrue(response["result"]["capabilities"]["editor"]["removeJsonTrailingCommas"])
        self.assertEqual(response["result"]["capabilities"]["editor"]["formatDocument"], ["json"])
        self.assertTrue(response["result"]["capabilities"]["editor"]["codeActions"])
        self.assertTrue(response["result"]["capabilities"]["editor"]["resolveCodeAction"])
        self.assertTrue(response["result"]["capabilities"]["editor"]["workspaceEditPayload"])
        self.assertIn("content-length", response["result"]["capabilities"]["transport"]["framing"])

    def test_workspace_apply_edit_and_diagnostics(self):
        asyncio.run(self.sandbox.write_file("demo.py", "print('ok')\n", thread_id=self.thread.id))
        apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "edits": [{
                    "path": "demo.py",
                    "start_line": 1,
                    "end_line": 1,
                    "new_text": "if True print('broken')\n",
                }],
            },
        }))
        self.assertEqual(apply_response["result"]["applied"][0]["mode"], "range")

        read_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "demo.py"},
        }))
        self.assertIn("broken", read_response["result"]["content"])

        diagnostics_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "demo.py"},
        }))
        self.assertEqual(len(diagnostics_response["result"]["diagnostics"]), 1)
        self.assertEqual(diagnostics_response["result"]["diagnostics"][0]["severity"], "error")

    def test_workspace_apply_edit_accepts_workspace_edit_payload(self):
        asyncio.run(self.sandbox.write_file("demo.py", "foo = 1\n", thread_id=self.thread.id))

        apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 44,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "workspace_edit": {
                    "document_changes": [{
                        "path": "demo.py",
                        "kind": "textEdits",
                        "edits": [{
                            "range": {
                                "start": {"line": 1, "column": 0},
                                "end": {"line": 1, "column": 3},
                            },
                            "old_text": "foo",
                            "new_text": "bar",
                        }],
                    }],
                    "change_count": 1,
                },
            },
        }))
        self.assertEqual(apply_response["result"]["applied"][0]["mode"], "textEdits")
        self.assertEqual(apply_response["result"]["applied"][0]["edits"], 1)

        read_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 45,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "demo.py"},
        }))
        self.assertEqual(read_response["result"]["content"], "bar = 1\n")

    def test_local_bind_and_system_info(self):
        bind_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "local/bindThread",
            "params": {"thread_id": self.thread.id, "client_id": self.client.client_id},
        }))
        self.assertEqual(bind_response["result"]["status"], "bound")

        context_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "workspace/context",
            "params": {"thread_id": self.thread.id},
        }))
        self.assertEqual(context_response["result"]["local_client"]["client_id"], self.client.client_id)
        self.assertEqual(context_response["result"]["runtime"]["backend"], "fake-sandbox")

        info_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "local/getSystemInfo",
            "params": {"thread_id": self.thread.id},
        }))
        self.assertTrue(info_response["result"]["success"])
        self.assertEqual(self.client.calls[0][0], "get_system_info")

    def test_editor_state_round_trip_and_diagnostics_merge(self):
        asyncio.run(self.sandbox.write_file("demo.py", "if True print('broken')\n", thread_id=self.thread.id))

        update_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 8,
            "method": "editor/updateState",
            "params": {
                "thread_id": self.thread.id,
                "client_id": self.client.client_id,
                "state": {
                    "active_file": "demo.py",
                    "open_files": ["demo.py", "notes.txt"],
                    "cursor": {"line": 1, "column": 3},
                    "diagnostics": [{
                        "path": "demo.py",
                        "severity": "warning",
                        "message": "Editor warning",
                        "line": 1,
                        "column": 0,
                        "source": "pylance",
                    }],
                },
            },
        }))
        self.assertEqual(update_response["result"]["editor_state"]["active_file"], "demo.py")
        self.assertEqual(update_response["result"]["editor_state"]["client_id"], self.client.client_id)

        get_state_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 9,
            "method": "editor/getState",
            "params": {"thread_id": self.thread.id},
        }))
        self.assertEqual(get_state_response["result"]["editor_state"]["cursor"]["column"], 3)

        context_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "workspace/context",
            "params": {"thread_id": self.thread.id},
        }))
        self.assertEqual(context_response["result"]["editor_state"]["active_file"], "demo.py")

        diagnostics_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "demo.py"},
        }))
        self.assertEqual(len(diagnostics_response["result"]["diagnostics"]), 2)
        self.assertEqual(diagnostics_response["result"]["diagnostics"][0]["source"], "pylance")

        clear_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 12,
            "method": "editor/clearState",
            "params": {"thread_id": self.thread.id},
        }))
        self.assertTrue(clear_response["result"]["cleared"])

        get_cleared_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 13,
            "method": "editor/getState",
            "params": {"thread_id": self.thread.id},
        }))
        self.assertIsNone(get_cleared_response["result"]["editor_state"])

    def test_editor_subscribe_queues_notifications(self):
        subscribe_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 14,
            "method": "editor/subscribe",
            "params": {
                "thread_id": self.thread.id,
                "events": ["editorStateChanged", "editorDiagnosticsChanged"],
            },
        }))
        subscription_id = subscribe_response["result"]["subscription_id"]

        self.editor_state.update_state(
            self.thread.id,
            {
                "active_file": "demo.py",
                "diagnostics": [{"path": "demo.py", "severity": "warning", "message": "watch me", "line": 1, "column": 0}],
            },
            client_id="client-1",
        )

        notifications = self.server._drain_notifications()
        methods = [item["method"] for item in notifications]
        self.assertIn("notifications/editorStateChanged", methods)
        self.assertIn("notifications/editorDiagnosticsChanged", methods)
        self.assertTrue(all(item["params"]["subscription_id"] == subscription_id for item in notifications))

        unsubscribe_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 15,
            "method": "editor/unsubscribe",
            "params": {"subscription_id": subscription_id},
        }))
        self.assertTrue(unsubscribe_response["result"]["unsubscribed"])

        self.editor_state.update_state(self.thread.id, {"active_file": "demo2.py"}, client_id="client-1")
        self.assertEqual(self.server._drain_notifications(), [])

    def test_editor_rename_symbol_updates_cross_file_js_references(self):
        asyncio.run(self.sandbox.write_file(
            "module.ts",
            "export const foo = 1\nexport function readFoo() {\n    return foo\n}\n",
            thread_id=self.thread.id,
        ))
        asyncio.run(self.sandbox.write_file(
            "consumer.ts",
            "import { foo, readFoo } from './module'\nconsole.log(foo, readFoo())\n",
            thread_id=self.thread.id,
        ))
        self.editor_state.update_state(
            self.thread.id,
            {"active_file": "module.ts", "cursor": {"line": 1, "column": 13}},
            client_id=self.client.client_id,
        )

        rename_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 16,
            "method": "editor/renameSymbol",
            "params": {
                "thread_id": self.thread.id,
                "line": 1,
                "column": 13,
                "new_name": "renamedFoo",
            },
        }))

        self.assertEqual(rename_response["result"]["symbol"], "foo")
        self.assertEqual(rename_response["result"]["new_name"], "renamedFoo")
        self.assertEqual(rename_response["result"]["files_changed"], 2)
        self.assertGreaterEqual(rename_response["result"]["occurrences_changed"], 3)
        self.assertEqual(rename_response["result"]["workspace_edit"]["change_count"], rename_response["result"]["occurrences_changed"])
        self.assertEqual(rename_response["result"]["workspace_edit"]["document_changes"][0]["kind"], "textEdits")

        module_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 17,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "module.ts"},
        }))
        consumer_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 18,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "consumer.ts"},
        }))
        self.assertIn("renamedFoo", module_read["result"]["content"])
        self.assertIn("return renamedFoo", module_read["result"]["content"])
        self.assertIn("import { renamedFoo, readFoo }", consumer_read["result"]["content"])
        self.assertIn("console.log(renamedFoo, readFoo())", consumer_read["result"]["content"])

    def test_editor_find_references_and_apply_code_action(self):
        asyncio.run(self.sandbox.write_file(
            "module.ts",
            "export const foo = 1\nexport function readFoo() {\n    return foo\n}\n",
            thread_id=self.thread.id,
        ))
        asyncio.run(self.sandbox.write_file(
            "consumer.ts",
            "import { foo, readFoo } from './module'\nconsole.log(foo, readFoo())\n",
            thread_id=self.thread.id,
        ))
        self.editor_state.update_state(
            self.thread.id,
            {"active_file": "module.ts", "cursor": {"line": 1, "column": 13}},
            client_id=self.client.client_id,
        )

        refs_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 19,
            "method": "editor/findReferences",
            "params": {"thread_id": self.thread.id, "line": 1, "column": 13},
        }))
        self.assertEqual(refs_response["result"]["symbol"], "foo")
        self.assertGreaterEqual(refs_response["result"]["count"], 4)
        self.assertEqual(refs_response["result"]["references"][0]["path"], "consumer.ts")

        actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 20,
            "method": "editor/codeActions",
            "params": {"thread_id": self.thread.id, "line": 1, "column": 13},
        }))
        action = actions_response["result"]["actions"][0]
        self.assertEqual(action["kind"], "refactor.rename")
        self.assertEqual(action["command"], "editor/renameSymbol")
        self.assertEqual(action["data"]["symbol"], "foo")

        apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 21,
            "method": "editor/applyCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": action,
                "inputs": {"new_name": "appliedFoo"},
                "apply_mode": "workspace_edit",
            },
        }))
        self.assertFalse(apply_response["result"]["applied"])
        self.assertEqual(apply_response["result"]["apply_mode"], "workspace_edit")
        self.assertEqual(apply_response["result"]["result"]["new_name"], "appliedFoo")
        self.assertEqual(apply_response["result"]["workspace_edit"]["change_count"], apply_response["result"]["result"]["occurrences_changed"])

        pre_apply_module = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 46,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "module.ts"},
        }))
        self.assertNotIn("appliedFoo", pre_apply_module["result"]["content"])

        workspace_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 47,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "workspace_edit": apply_response["result"]["workspace_edit"],
            },
        }))
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["mode"], "textEdits")

        module_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 22,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "module.ts"},
        }))
        consumer_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 23,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "consumer.ts"},
        }))
        self.assertIn("appliedFoo", module_read["result"]["content"])
        self.assertIn("appliedFoo", consumer_read["result"]["content"])

    def test_editor_prepare_rename_and_resolve_code_action_preview(self):
        asyncio.run(self.sandbox.write_file(
            "module.ts",
            "export const foo = 1\nexport function readFoo() {\n    return foo\n}\n",
            thread_id=self.thread.id,
        ))
        asyncio.run(self.sandbox.write_file(
            "consumer.ts",
            "import { foo, readFoo } from './module'\nconsole.log(foo, readFoo())\n",
            thread_id=self.thread.id,
        ))
        self.editor_state.update_state(
            self.thread.id,
            {"active_file": "module.ts", "cursor": {"line": 1, "column": 13}},
            client_id=self.client.client_id,
        )

        prepare_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 24,
            "method": "editor/prepareRename",
            "params": {"thread_id": self.thread.id, "line": 1, "column": 13},
        }))
        self.assertTrue(prepare_response["result"]["can_rename"])
        self.assertEqual(prepare_response["result"]["symbol"], "foo")
        self.assertEqual(prepare_response["result"]["placeholder"], "foo")
        self.assertEqual(prepare_response["result"]["range"]["start"], {"line": 1, "column": 13})
        self.assertEqual(prepare_response["result"]["range"]["end"], {"line": 1, "column": 16})
        self.assertGreaterEqual(prepare_response["result"]["reference_count"], 4)

        actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 25,
            "method": "editor/codeActions",
            "params": {"thread_id": self.thread.id, "line": 1, "column": 13},
        }))
        action = actions_response["result"]["actions"][0]

        resolve_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 26,
            "method": "editor/resolveCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": action,
                "inputs": {"new_name": "previewFoo"},
            },
        }))
        self.assertTrue(resolve_response["result"]["resolved"])
        preview = resolve_response["result"]["preview"]
        self.assertEqual(preview["symbol"], "foo")
        self.assertEqual(preview["new_name"], "previewFoo")
        self.assertEqual(preview["files_changed"], 2)
        self.assertGreaterEqual(preview["occurrences_changed"], 3)
        self.assertEqual(preview["edits"][0]["path"], "consumer.ts")
        self.assertEqual(preview["edits"][0]["edits"][0]["old_text"], "foo")
        self.assertEqual(preview["edits"][0]["edits"][0]["new_text"], "previewFoo")
        self.assertEqual(preview["workspace_edit"]["change_count"], preview["occurrences_changed"])
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["kind"], "textEdits")

        module_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 27,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "module.ts"},
        }))
        consumer_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 28,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "consumer.ts"},
        }))
        self.assertNotIn("previewFoo", module_read["result"]["content"])
        self.assertNotIn("previewFoo", consumer_read["result"]["content"])

    def test_editor_code_actions_supports_only_filters(self):
        invalid_script = "const foo = 1\nconsole.log(foo)\n"
        asyncio.run(self.sandbox.write_file("filtered.ts", invalid_script, thread_id=self.thread.id))
        self.editor_state.update_state(
            self.thread.id,
            {
                "active_file": "filtered.ts",
                "cursor": {"line": 1, "column": 6},
                "diagnostics": [
                    {"path": "filtered.ts", "severity": "error", "message": "Missing semicolon.", "line": 1, "column": 13, "source": "eslint"},
                    {"path": "filtered.ts", "severity": "error", "message": "Missing semicolon.", "line": 2, "column": 16, "source": "eslint"},
                ],
            },
            client_id=self.client.client_id,
        )

        all_actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 29,
            "method": "editor/codeActions",
            "params": {"thread_id": self.thread.id, "line": 1, "column": 6},
        }))
        all_kinds = {action["kind"] for action in all_actions_response["result"]["actions"]}
        self.assertEqual(all_kinds, {"refactor.rename", "quickfix.script.insertMissingSemicolons"})

        exact_filter_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 30,
            "method": "editor/codeActions",
            "params": {
                "thread_id": self.thread.id,
                "line": 1,
                "column": 6,
                "only": ["quickfix.script.insertMissingSemicolons"],
            },
        }))
        exact_kinds = [action["kind"] for action in exact_filter_response["result"]["actions"]]
        self.assertEqual(exact_kinds, ["quickfix.script.insertMissingSemicolons"])

        prefix_filter_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 31,
            "method": "editor/codeActions",
            "params": {
                "thread_id": self.thread.id,
                "line": 1,
                "column": 6,
                "context": {"only": ["quickfix"]},
            },
        }))
        prefix_kinds = {action["kind"] for action in prefix_filter_response["result"]["actions"]}
        self.assertEqual(prefix_kinds, {"quickfix.script.insertMissingSemicolons"})

    def test_editor_code_actions_honor_context_diagnostics_for_quickfix_scope(self):
        invalid_script = "const foo = 1\nconsole.log(foo)\n"
        diagnostics = [
            {"path": "scoped.ts", "severity": "error", "message": "Missing semicolon.", "line": 1, "column": 13, "source": "eslint"},
            {"path": "scoped.ts", "severity": "error", "message": "Missing semicolon.", "line": 2, "column": 16, "source": "eslint"},
        ]
        asyncio.run(self.sandbox.write_file("scoped.ts", invalid_script, thread_id=self.thread.id))
        self.editor_state.update_state(
            self.thread.id,
            {
                "active_file": "scoped.ts",
                "diagnostics": diagnostics,
            },
            client_id=self.client.client_id,
        )

        actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 32,
            "method": "editor/codeActions",
            "params": {
                "thread_id": self.thread.id,
                "context": {
                    "diagnostics": [diagnostics[0]],
                    "only": ["quickfix"],
                },
            },
        }))
        self.assertEqual(len(actions_response["result"]["actions"]), 1)
        quick_fix = actions_response["result"]["actions"][0]
        self.assertEqual(quick_fix["kind"], "quickfix.script.insertMissingSemicolons")
        self.assertEqual(quick_fix["data"]["fix_count"], 1)
        self.assertEqual(len(quick_fix["arguments"]["diagnostics"]), 1)
        self.assertEqual(quick_fix["arguments"]["diagnostics"][0]["line"], 1)

        resolve_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 40,
            "method": "editor/resolveCodeAction",
            "params": {"thread_id": self.thread.id, "action": quick_fix},
        }))
        preview = resolve_response["result"]["preview"]
        self.assertTrue(preview["changed"])
        self.assertEqual(preview["diagnostic_count"], 1)
        self.assertEqual(len(preview["fixes"]), 1)
        self.assertEqual(preview["fixes"][0]["line"], 1)
        self.assertEqual(preview["workspace_edit"]["change_count"], 1)

        deferred_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 41,
            "method": "editor/applyCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": quick_fix,
                "apply_mode": "workspace_edit",
            },
        }))
        self.assertFalse(deferred_apply_response["result"]["applied"])
        self.assertEqual(deferred_apply_response["result"]["result"]["fixes_applied"], 1)
        self.assertEqual(deferred_apply_response["result"]["workspace_edit"]["change_count"], 1)

        workspace_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 42,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "workspace_edit": deferred_apply_response["result"]["workspace_edit"],
            },
        }))
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["edits"], 1)

        fixed_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 43,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "scoped.ts"},
        }))
        self.assertEqual(fixed_read["result"]["content"], "const foo = 1;\nconsole.log(foo)\n")

    def test_editor_format_document_and_json_code_action_flow(self):
        asyncio.run(self.sandbox.write_file(
            "data.json",
            '{"b":2,"a":{"nested":true}}',
            thread_id=self.thread.id,
        ))
        self.editor_state.update_state(
            self.thread.id,
            {"active_file": "data.json"},
            client_id=self.client.client_id,
        )

        actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 33,
            "method": "editor/codeActions",
            "params": {"thread_id": self.thread.id},
        }))
        action = actions_response["result"]["actions"][0]
        self.assertEqual(action["kind"], "source.formatDocument")
        self.assertEqual(action["command"], "editor/formatDocument")

        resolve_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 34,
            "method": "editor/resolveCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": action,
                "inputs": {"indent": 4},
            },
        }))
        preview = resolve_response["result"]["preview"]
        self.assertTrue(preview["changed"])
        self.assertEqual(preview["files_changed"], 1)
        self.assertEqual(preview["edits"][0]["path"], "data.json")
        self.assertEqual(preview["edits"][0]["mode"], "replaceDocument")
        self.assertEqual(preview["workspace_edit"]["change_count"], 1)
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["kind"], "replaceDocument")

        unchanged_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 35,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "data.json"},
        }))
        self.assertEqual(unchanged_read["result"]["content"], '{"b":2,"a":{"nested":true}}')

        apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 36,
            "method": "editor/applyCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": action,
                "inputs": {"indent": 4},
                "apply_mode": "workspace_edit",
            },
        }))
        self.assertFalse(apply_response["result"]["applied"])
        self.assertEqual(apply_response["result"]["apply_mode"], "workspace_edit")
        self.assertTrue(apply_response["result"]["result"]["changed"])
        self.assertEqual(apply_response["result"]["workspace_edit"]["document_changes"][0]["kind"], "replaceDocument")

        still_unformatted_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 48,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "data.json"},
        }))
        self.assertEqual(still_unformatted_read["result"]["content"], '{"b":2,"a":{"nested":true}}')

        workspace_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 49,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "workspace_edit": apply_response["result"]["workspace_edit"],
            },
        }))
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["mode"], "replaceDocument")

        formatted_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 37,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "data.json"},
        }))
        self.assertIn('\n    "a": {', formatted_read["result"]["content"])
        self.assertTrue(formatted_read["result"]["content"].endswith("\n"))

        asyncio.run(self.sandbox.write_file(
            "data.json",
            '{"x":1}',
            thread_id=self.thread.id,
        ))
        direct_format_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 38,
            "method": "editor/formatDocument",
            "params": {"thread_id": self.thread.id, "path": "data.json", "indent": 2},
        }))
        self.assertTrue(direct_format_response["result"]["changed"])
        self.assertEqual(direct_format_response["result"]["workspace_edit"]["document_changes"][0]["kind"], "replaceDocument")

        direct_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 39,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "data.json"},
        }))
        self.assertEqual(direct_read["result"]["content"], '{\n  "x": 1\n}\n')

    def test_editor_json_trailing_comma_quick_fix_flow(self):
        invalid_json = '{\n  "items": [\n    1,\n    2,\n  ],\n}\n'
        asyncio.run(self.sandbox.write_file("broken.json", invalid_json, thread_id=self.thread.id))
        self.editor_state.update_state(
            self.thread.id,
            {"active_file": "broken.json"},
            client_id=self.client.client_id,
        )

        diagnostics_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 54,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "broken.json"},
        }))
        self.assertGreaterEqual(len(diagnostics_response["result"]["diagnostics"]), 1)

        actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 55,
            "method": "editor/codeActions",
            "params": {"thread_id": self.thread.id},
        }))
        quick_fix = actions_response["result"]["actions"][0]
        self.assertEqual(quick_fix["kind"], "quickfix.json.removeTrailingCommas")
        self.assertEqual(quick_fix["command"], "editor/removeJsonTrailingCommas")

        resolve_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 56,
            "method": "editor/resolveCodeAction",
            "params": {"thread_id": self.thread.id, "action": quick_fix},
        }))
        preview = resolve_response["result"]["preview"]
        self.assertTrue(preview["changed"])
        self.assertGreaterEqual(preview["diagnostic_count"], 1)
        self.assertEqual(len(preview["fixes"]), 2)
        self.assertEqual(preview["workspace_edit"]["change_count"], 2)
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["kind"], "textEdits")

        deferred_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 57,
            "method": "editor/applyCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": quick_fix,
                "apply_mode": "workspace_edit",
            },
        }))
        self.assertFalse(deferred_apply_response["result"]["applied"])
        self.assertEqual(deferred_apply_response["result"]["workspace_edit"]["change_count"], 2)

        pre_apply_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 58,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "broken.json"},
        }))
        self.assertEqual(pre_apply_read["result"]["content"], invalid_json)

        workspace_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 59,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "workspace_edit": deferred_apply_response["result"]["workspace_edit"],
            },
        }))
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["mode"], "textEdits")
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["edits"], 2)

        fixed_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 60,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "broken.json"},
        }))
        self.assertEqual(fixed_read["result"]["content"], '{\n  "items": [\n    1,\n    2\n  ]\n}\n')

        fixed_diagnostics = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 61,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "broken.json"},
        }))
        self.assertEqual(fixed_diagnostics["result"]["diagnostics"], [])

        asyncio.run(self.sandbox.write_file("broken.json", invalid_json, thread_id=self.thread.id))
        direct_fix_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 62,
            "method": "editor/removeJsonTrailingCommas",
            "params": {"thread_id": self.thread.id, "path": "broken.json"},
        }))
        self.assertTrue(direct_fix_response["result"]["changed"])
        self.assertEqual(direct_fix_response["result"]["fixes_applied"], 2)
        self.assertEqual(direct_fix_response["result"]["workspace_edit"]["change_count"], 2)

    def test_editor_json_comment_quick_fix_flow(self):
        invalid_json = '{\n  // comment\n  "value": 1 /* inline */\n}\n'
        asyncio.run(self.sandbox.write_file("commented.json", invalid_json, thread_id=self.thread.id))
        self.editor_state.update_state(
            self.thread.id,
            {"active_file": "commented.json"},
            client_id=self.client.client_id,
        )

        diagnostics_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 67,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "commented.json"},
        }))
        self.assertGreaterEqual(len(diagnostics_response["result"]["diagnostics"]), 1)

        actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 68,
            "method": "editor/codeActions",
            "params": {"thread_id": self.thread.id},
        }))
        quick_fix = next(action for action in actions_response["result"]["actions"] if action["kind"] == "quickfix.json.removeComments")
        self.assertEqual(quick_fix["command"], "editor/removeJsonComments")

        resolve_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 69,
            "method": "editor/resolveCodeAction",
            "params": {"thread_id": self.thread.id, "action": quick_fix},
        }))
        preview = resolve_response["result"]["preview"]
        self.assertTrue(preview["changed"])
        self.assertGreaterEqual(preview["diagnostic_count"], 1)
        self.assertEqual(len(preview["fixes"]), 2)
        self.assertEqual(preview["workspace_edit"]["change_count"], 2)
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["kind"], "textEdits")

        deferred_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 70,
            "method": "editor/applyCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": quick_fix,
                "apply_mode": "workspace_edit",
            },
        }))
        self.assertFalse(deferred_apply_response["result"]["applied"])
        self.assertEqual(deferred_apply_response["result"]["workspace_edit"]["change_count"], 2)

        pre_apply_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 71,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "commented.json"},
        }))
        self.assertEqual(pre_apply_read["result"]["content"], invalid_json)

        workspace_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 72,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "workspace_edit": deferred_apply_response["result"]["workspace_edit"],
            },
        }))
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["mode"], "textEdits")
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["edits"], 2)

        fixed_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 73,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "commented.json"},
        }))
        self.assertEqual(fixed_read["result"]["content"], '{\n  \n  "value": 1 \n}\n')

        fixed_diagnostics = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 74,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "commented.json"},
        }))
        self.assertEqual(fixed_diagnostics["result"]["diagnostics"], [])

        asyncio.run(self.sandbox.write_file("commented.json", invalid_json, thread_id=self.thread.id))
        direct_fix_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 75,
            "method": "editor/removeJsonComments",
            "params": {"thread_id": self.thread.id, "path": "commented.json"},
        }))
        self.assertTrue(direct_fix_response["result"]["changed"])
        self.assertEqual(direct_fix_response["result"]["fixes_applied"], 2)
        self.assertEqual(direct_fix_response["result"]["workspace_edit"]["change_count"], 2)

    def test_editor_python_missing_colon_quick_fix_flow(self):
        invalid_python = "if True\n    pass\n"
        asyncio.run(self.sandbox.write_file("broken.py", invalid_python, thread_id=self.thread.id))
        self.editor_state.update_state(
            self.thread.id,
            {"active_file": "broken.py"},
            client_id=self.client.client_id,
        )

        diagnostics_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 80,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "broken.py"},
        }))
        self.assertEqual(diagnostics_response["result"]["diagnostics"][0]["message"], "expected ':'")

        actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 81,
            "method": "editor/codeActions",
            "params": {"thread_id": self.thread.id},
        }))
        quick_fix = next(action for action in actions_response["result"]["actions"] if action["kind"] == "quickfix.python.insertMissingColon")
        self.assertEqual(quick_fix["command"], "editor/insertPythonMissingColon")

        resolve_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 82,
            "method": "editor/resolveCodeAction",
            "params": {"thread_id": self.thread.id, "action": quick_fix},
        }))
        preview = resolve_response["result"]["preview"]
        self.assertTrue(preview["changed"])
        self.assertEqual(preview["diagnostic_count"], 1)
        self.assertEqual(len(preview["fixes"]), 1)
        self.assertEqual(preview["workspace_edit"]["change_count"], 1)
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["kind"], "textEdits")
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["edits"][0]["new_text"], ":")

        deferred_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 83,
            "method": "editor/applyCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": quick_fix,
                "apply_mode": "workspace_edit",
            },
        }))
        self.assertFalse(deferred_apply_response["result"]["applied"])
        self.assertEqual(deferred_apply_response["result"]["workspace_edit"]["change_count"], 1)

        pre_apply_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 84,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "broken.py"},
        }))
        self.assertEqual(pre_apply_read["result"]["content"], invalid_python)

        workspace_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 85,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "workspace_edit": deferred_apply_response["result"]["workspace_edit"],
            },
        }))
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["mode"], "textEdits")
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["edits"], 1)

        fixed_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 86,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "broken.py"},
        }))
        self.assertEqual(fixed_read["result"]["content"], "if True:\n    pass\n")

        fixed_diagnostics = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 87,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "broken.py"},
        }))
        self.assertEqual(fixed_diagnostics["result"]["diagnostics"], [])

        asyncio.run(self.sandbox.write_file("broken.py", invalid_python, thread_id=self.thread.id))
        direct_fix_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 88,
            "method": "editor/insertPythonMissingColon",
            "params": {"thread_id": self.thread.id, "path": "broken.py"},
        }))
        self.assertTrue(direct_fix_response["result"]["changed"])
        self.assertEqual(direct_fix_response["result"]["fixes_applied"], 1)
        self.assertEqual(direct_fix_response["result"]["workspace_edit"]["change_count"], 1)

    def test_editor_python_unused_import_quick_fix_flow(self):
        invalid_python = "import os, sys as system\nfrom pathlib import Path\nprint(os.getcwd())\n"
        asyncio.run(self.sandbox.write_file("linty.py", invalid_python, thread_id=self.thread.id))
        diagnostics = [
            {"path": "linty.py", "severity": "warning", "code": "F401", "message": "`system` imported but unused", "line": 1, "column": 11, "source": "ruff"},
            {"path": "linty.py", "severity": "warning", "code": "F401", "message": "'Path' imported but unused", "line": 2, "column": 20, "source": "ruff"},
        ]
        self.editor_state.update_state(
            self.thread.id,
            {
                "active_file": "linty.py",
                "diagnostics": diagnostics,
            },
            client_id=self.client.client_id,
        )

        diagnostics_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 106,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "linty.py"},
        }))
        self.assertEqual(len(diagnostics_response["result"]["diagnostics"]), 2)

        actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 107,
            "method": "editor/codeActions",
            "params": {"thread_id": self.thread.id},
        }))
        quick_fix = next(action for action in actions_response["result"]["actions"] if action["kind"] == "quickfix.python.removeUnusedImports")
        self.assertEqual(quick_fix["command"], "editor/removeUnusedPythonImports")

        resolve_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 108,
            "method": "editor/resolveCodeAction",
            "params": {"thread_id": self.thread.id, "action": quick_fix},
        }))
        preview = resolve_response["result"]["preview"]
        self.assertTrue(preview["changed"])
        self.assertEqual(preview["diagnostic_count"], 2)
        self.assertEqual(len(preview["fixes"]), 2)
        self.assertEqual(preview["workspace_edit"]["change_count"], 2)
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["kind"], "textEdits")
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["edits"][0]["new_text"], "import os")

        deferred_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 109,
            "method": "editor/applyCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": quick_fix,
                "apply_mode": "workspace_edit",
            },
        }))
        self.assertFalse(deferred_apply_response["result"]["applied"])
        self.assertEqual(deferred_apply_response["result"]["workspace_edit"]["change_count"], 2)

        pre_apply_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 110,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "linty.py"},
        }))
        self.assertEqual(pre_apply_read["result"]["content"], invalid_python)

        workspace_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 111,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "workspace_edit": deferred_apply_response["result"]["workspace_edit"],
            },
        }))
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["mode"], "textEdits")
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["edits"], 2)

        fixed_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 112,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "linty.py"},
        }))
        self.assertEqual(fixed_read["result"]["content"], "import os\nprint(os.getcwd())\n")

        self.editor_state.update_state(
            self.thread.id,
            {"active_file": "linty.py", "diagnostics": []},
            client_id=self.client.client_id,
        )
        cleared_diagnostics = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 113,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "linty.py"},
        }))
        self.assertEqual(cleared_diagnostics["result"]["diagnostics"], [])

        asyncio.run(self.sandbox.write_file("linty.py", invalid_python, thread_id=self.thread.id))
        self.editor_state.update_state(
            self.thread.id,
            {
                "active_file": "linty.py",
                "diagnostics": diagnostics,
            },
            client_id=self.client.client_id,
        )
        direct_fix_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 114,
            "method": "editor/removeUnusedPythonImports",
            "params": {"thread_id": self.thread.id, "path": "linty.py"},
        }))
        self.assertTrue(direct_fix_response["result"]["changed"])
        self.assertEqual(direct_fix_response["result"]["fixes_applied"], 2)
        self.assertEqual(direct_fix_response["result"]["workspace_edit"]["change_count"], 2)

    def test_editor_js_missing_semicolon_quick_fix_flow(self):
        invalid_script = "const foo = 1\nconsole.log(foo)\n"
        asyncio.run(self.sandbox.write_file("script.ts", invalid_script, thread_id=self.thread.id))
        self.editor_state.update_state(
            self.thread.id,
            {
                "active_file": "script.ts",
                "diagnostics": [
                    {"path": "script.ts", "severity": "error", "message": "Missing semicolon.", "line": 1, "column": 13, "source": "eslint"},
                    {"path": "script.ts", "severity": "error", "message": "Missing semicolon.", "line": 2, "column": 16, "source": "eslint"},
                ],
            },
            client_id=self.client.client_id,
        )

        diagnostics_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 93,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "script.ts"},
        }))
        self.assertEqual(len(diagnostics_response["result"]["diagnostics"]), 2)

        actions_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 94,
            "method": "editor/codeActions",
            "params": {"thread_id": self.thread.id},
        }))
        quick_fix = next(action for action in actions_response["result"]["actions"] if action["kind"] == "quickfix.script.insertMissingSemicolons")
        self.assertEqual(quick_fix["command"], "editor/insertMissingSemicolons")

        resolve_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 95,
            "method": "editor/resolveCodeAction",
            "params": {"thread_id": self.thread.id, "action": quick_fix},
        }))
        preview = resolve_response["result"]["preview"]
        self.assertTrue(preview["changed"])
        self.assertEqual(preview["diagnostic_count"], 2)
        self.assertEqual(len(preview["fixes"]), 2)
        self.assertEqual(preview["workspace_edit"]["change_count"], 2)
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["kind"], "textEdits")
        self.assertEqual(preview["workspace_edit"]["document_changes"][0]["edits"][0]["new_text"], ";")

        deferred_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 96,
            "method": "editor/applyCodeAction",
            "params": {
                "thread_id": self.thread.id,
                "action": quick_fix,
                "apply_mode": "workspace_edit",
            },
        }))
        self.assertFalse(deferred_apply_response["result"]["applied"])
        self.assertEqual(deferred_apply_response["result"]["workspace_edit"]["change_count"], 2)

        pre_apply_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 97,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "script.ts"},
        }))
        self.assertEqual(pre_apply_read["result"]["content"], invalid_script)

        workspace_apply_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 98,
            "method": "workspace/applyEdit",
            "params": {
                "thread_id": self.thread.id,
                "workspace_edit": deferred_apply_response["result"]["workspace_edit"],
            },
        }))
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["mode"], "textEdits")
        self.assertEqual(workspace_apply_response["result"]["applied"][0]["edits"], 2)

        fixed_read = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 99,
            "method": "workspace/readFile",
            "params": {"thread_id": self.thread.id, "path": "script.ts"},
        }))
        self.assertEqual(fixed_read["result"]["content"], "const foo = 1;\nconsole.log(foo);\n")

        self.editor_state.update_state(
            self.thread.id,
            {"active_file": "script.ts", "diagnostics": []},
            client_id=self.client.client_id,
        )
        cleared_diagnostics = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 100,
            "method": "workspace/diagnostics",
            "params": {"thread_id": self.thread.id, "path": "script.ts"},
        }))
        self.assertEqual(cleared_diagnostics["result"]["diagnostics"], [])

        asyncio.run(self.sandbox.write_file("script.ts", invalid_script, thread_id=self.thread.id))
        self.editor_state.update_state(
            self.thread.id,
            {
                "active_file": "script.ts",
                "diagnostics": [
                    {"path": "script.ts", "severity": "error", "message": "Missing semicolon.", "line": 1, "column": 13, "source": "eslint"},
                    {"path": "script.ts", "severity": "error", "message": "Missing semicolon.", "line": 2, "column": 16, "source": "eslint"},
                ],
            },
            client_id=self.client.client_id,
        )
        direct_fix_response = asyncio.run(self.server.handle_rpc({
            "jsonrpc": "2.0",
            "id": 101,
            "method": "editor/insertMissingSemicolons",
            "params": {"thread_id": self.thread.id, "path": "script.ts"},
        }))
        self.assertTrue(direct_fix_response["result"]["changed"])
        self.assertEqual(direct_fix_response["result"]["fixes_applied"], 2)
        self.assertEqual(direct_fix_response["result"]["workspace_edit"]["change_count"], 2)

    def test_stdio_content_length_framing(self):
        initialize = {"jsonrpc": "2.0", "id": 102, "method": "initialize", "params": {}}
        subscribe = {
            "jsonrpc": "2.0",
            "id": 103,
            "method": "editor/subscribe",
            "params": {"thread_id": self.thread.id, "events": ["editorStateChanged", "editorDiagnosticsChanged"]},
        }
        update_state = {
            "jsonrpc": "2.0",
            "id": 104,
            "method": "editor/updateState",
            "params": {
                "thread_id": self.thread.id,
                "client_id": self.client.client_id,
                "state": {"active_file": "demo.py", "diagnostics": [{"path": "demo.py", "severity": "error", "message": "boom", "line": 1, "column": 0}]},
            },
        }
        shutdown = {"jsonrpc": "2.0", "id": 105, "method": "shutdown", "params": {}}

        def encode(payload: dict) -> bytes:
            raw = json.dumps(payload).encode("utf-8")
            return f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8") + raw

        stdin = io.BytesIO(encode(initialize) + encode(subscribe) + encode(update_state) + encode(shutdown))
        stdout = io.BytesIO()
        asyncio.run(self.server.serve_forever(stdin=stdin, stdout=stdout))

        data = stdout.getvalue()
        self.assertIn(b"Content-Length:", data)
        self.assertIn(b"acp-lite-0.1", data)
        self.assertIn(b'"ok": true', data)
        self.assertIn(b"notifications/editorStateChanged", data)
        self.assertIn(b"notifications/editorDiagnosticsChanged", data)


if __name__ == "__main__":
    unittest.main()
