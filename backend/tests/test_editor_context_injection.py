import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import app.local.editor_state as editor_state_module
from app.agents.super_agent import super_agent
from app.local.agent import local_agent


class _FakeAgent:
    def __init__(self, capture: dict):
        self.capture = capture

    async def astream_events(self, payload, version="v2"):
        self.capture["messages"] = payload["messages"]
        if False:
            yield {}


async def _drain(async_iterable):
    events = []
    async for item in async_iterable:
        events.append(json.loads(item))
    return events


def _message_text(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


class TestEditorContextInjection(unittest.TestCase):
    def setUp(self):
        self.original_store = editor_state_module.editor_state_store
        self.store = editor_state_module.EditorStateStore()
        editor_state_module.editor_state_store = self.store

    def tearDown(self):
        editor_state_module.editor_state_store = self.original_store

    def test_build_editor_context_prompt_formats_state(self):
        self.store.update_state(
            "thread-1",
            {
                "active_file": "src/app.py",
                "open_files": ["src/app.py", "README.md"],
                "cursor": {"line": 12, "column": 4},
                "diagnostics": [
                    {
                        "path": "src/app.py",
                        "severity": "error",
                        "message": "unexpected indent",
                        "line": 12,
                        "column": 2,
                        "source": "pylance",
                    }
                ],
            },
            client_id="client-1",
        )

        prompt = editor_state_module.build_editor_context_prompt("thread-1")
        self.assertIn("## Active Editor Context", prompt)
        self.assertIn("Active file: src/app.py", prompt)
        self.assertIn("Cursor: line 12, column 4", prompt)
        self.assertIn("[error] src/app.py:12:2 (pylance): unexpected indent", prompt)

    def test_super_agent_standard_flow_includes_editor_context(self):
        capture = {}
        self.store.update_state(
            "thread-super",
            {
                "active_file": "pkg/main.py",
                "cursor": {"line": 7, "column": 1},
                "diagnostics": [
                    {"path": "pkg/main.py", "severity": "warning", "message": "unused import", "line": 2, "column": 0}
                ],
            },
            client_id="client-super",
        )

        with patch("app.agents.super_agent.get_all_tools", return_value=[]), \
             patch("app.agents.super_agent.llm_provider.get_chat_model", return_value=object()), \
             patch("app.agents.super_agent.create_react_agent", return_value=_FakeAgent(capture)), \
             patch("app.agents.super_agent.auto_compact", new=AsyncMock(return_value=(None, "none"))), \
             patch("app.agents.super_agent.should_summarize", return_value=False), \
             patch("app.agents.super_agent.get_messages_for_context", return_value=[]), \
             patch("app.agents.super_agent._load_context_files", return_value=""), \
             patch("app.agents.super_agent.should_suggest_prompt", return_value=False), \
             patch("app.agents.super_agent.memory_store.get_context_for_query", new=AsyncMock(return_value="")), \
             patch("app.agents.learning_loop.session_search_db.store"), \
             patch("app.agents.learning_loop.nudge_manager.tick", return_value=None), \
             patch("app.memory.extract_memories.memory_extractor.maybe_extract", new=AsyncMock(return_value=[])), \
             patch("app.agents.magic_docs.magic_docs.record_session"):
            events = asyncio.run(
                _drain(
                    super_agent._standard_flow(
                        message="fix current file",
                        thread_messages=[],
                        model=None,
                        thread_id="thread-super",
                        enable_speculation=False,
                    )
                )
            )

        self.assertEqual(events[-1]["type"], "done")
        system_message = capture["messages"][0]
        system_text = _message_text(system_message)
        self.assertIn("Active file: pkg/main.py", system_text)
        self.assertIn("Cursor: line 7, column 1", system_text)
        self.assertIn("unused import", system_text)

    def test_local_agent_includes_editor_context(self):
        capture = {}
        self.store.update_state(
            "thread-local",
            {
                "active_file": "frontend/page.tsx",
                "cursor": {"line": 21, "column": 8},
            },
            client_id="client-local",
        )

        with patch.dict(os.environ, {"MODELSCOPE_API_KEY": "test-key"}), \
             patch("app.local.gateway.local_gateway.get_client_for_thread", return_value=object()), \
             patch("app.local.gateway.local_gateway.list_clients", return_value=[]), \
             patch("app.local.agent.get_all_tools", return_value=[]), \
             patch("app.local.agent.LOCAL_TOOLS", []), \
             patch("app.local.agent.llm_provider.get_chat_model", return_value=object()), \
             patch("app.local.agent.create_react_agent", return_value=_FakeAgent(capture)), \
             patch("app.local.agent.should_summarize", return_value=False), \
             patch("app.local.agent.get_messages_for_context", return_value=[]), \
             patch("app.local.agent.memory_store.get_context_for_query", new=AsyncMock(return_value="")):
            events = asyncio.run(
                _drain(
                    local_agent.handle_message(
                        message="look at my current file",
                        thread_messages=[],
                        model=None,
                        thread_id="thread-local",
                    )
                )
            )

        self.assertEqual(events[-1]["type"], "done")
        system_message = capture["messages"][0]
        system_text = _message_text(system_message)
        self.assertIn("Active file: frontend/page.tsx", system_text)
        self.assertIn("Cursor: line 21, column 8", system_text)


if __name__ == "__main__":
    unittest.main()
