import json
import unittest

from app.agents import tools as agent_tools
from app.local.editor_state import EditorStateStore


class TestEditorStateTools(unittest.TestCase):
    def setUp(self):
        self.store = EditorStateStore()
        self.original_store = agent_tools.editor_state_store
        agent_tools.editor_state_store = self.store
        agent_tools.set_thread_context("editor-tools-thread")

    def tearDown(self):
        agent_tools.editor_state_store = self.original_store
        agent_tools.set_thread_context("_default")

    def test_tools_are_registered(self):
        tool_names = [tool.name for tool in agent_tools.get_all_tools(wrap=False)]
        self.assertIn("get_editor_state", tool_names)
        self.assertIn("get_editor_diagnostics", tool_names)

    def test_get_editor_state_returns_current_thread_state(self):
        self.store.update_state(
            "editor-tools-thread",
            {
                "active_file": "src/main.py",
                "open_files": ["src/main.py"],
                "cursor": {"line": 12, "column": 4},
                "selection": {"start_line": 12, "end_line": 12},
            },
            client_id="client-123",
        )

        payload = json.loads(agent_tools.get_editor_state.invoke({}))
        self.assertEqual(payload["active_file"], "src/main.py")
        self.assertEqual(payload["cursor"]["line"], 12)
        self.assertEqual(payload["client_id"], "client-123")

    def test_get_editor_diagnostics_can_filter_by_path(self):
        self.store.update_state(
            "editor-tools-thread",
            {
                "diagnostics": [
                    {"path": "src/main.py", "severity": "error", "message": "bad syntax", "line": 3, "column": 1},
                    {"path": "src/other.py", "severity": "warning", "message": "unused", "line": 7, "column": 0},
                ]
            },
            client_id="client-123",
        )

        filtered = json.loads(agent_tools.get_editor_diagnostics.invoke({"path": "src/main.py"}))
        self.assertEqual(filtered["path"], "src/main.py")
        self.assertEqual(len(filtered["diagnostics"]), 1)
        self.assertEqual(filtered["diagnostics"][0]["message"], "bad syntax")

        all_diags = json.loads(agent_tools.get_editor_diagnostics.invoke({}))
        self.assertEqual(len(all_diags["diagnostics"]), 2)


if __name__ == "__main__":
    unittest.main()
