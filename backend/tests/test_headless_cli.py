import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

from app.agents.store import ThreadStore
from app.agents.tools import get_all_tools
from app.headless_cli import HeadlessCLIConfig, build_parser, config_from_args, run_headless


class TestHeadlessCLIConfig(unittest.TestCase):
    def test_config_from_args_splits_tool_lists(self):
        parser = build_parser()
        args = parser.parse_args([
            "--message", "hello",
            "--allowed-tools", "read_file,list_files",
            "--allowedTools", "get_current_time",
            "--disallowed-tools", "execute_bash,write_file",
            "--skill", "researcher",
        ])
        config, json_events = config_from_args(args)
        self.assertEqual(config.message, "hello")
        self.assertEqual(config.allowed_tools, ["read_file", "list_files", "get_current_time"])
        self.assertEqual(config.disallowed_tools, ["execute_bash", "write_file"])
        self.assertEqual(config.skills, ["researcher"])
        self.assertFalse(json_events)


class TestHeadlessCLIRun(unittest.TestCase):
    def test_run_headless_applies_tool_filter_and_persists_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ThreadStore(storage_path=tmp)
            seen: dict[str, object] = {}

            async def fake_handle_message(**kwargs):
                seen["thread_id"] = kwargs["thread_id"]
                seen["tool_names"] = [tool.name for tool in get_all_tools(wrap=False)]
                yield json.dumps({"type": "token", "content": "done"})
                yield json.dumps({"type": "done", "usage": {"input_tokens": 1, "output_tokens": 1}})

            config = HeadlessCLIConfig(
                message="inspect tools",
                thread_id="cli-test-thread",
                allowed_tools=["read_file", "list_files"],
                disallowed_tools=["list_files"],
            )

            with patch("app.headless_cli.thread_store", store), \
                 patch("app.headless_cli.super_agent.handle_message", fake_handle_message):
                result = asyncio.run(run_headless(config))

            self.assertEqual(result["thread_id"], "cli-test-thread")
            self.assertEqual(result["output"], "done")
            self.assertEqual(result["usage"], {"input_tokens": 1, "output_tokens": 1})
            self.assertEqual(seen["thread_id"], "cli-test-thread")
            self.assertEqual(seen["tool_names"], ["read_file"])

            saved = asyncio.run(store.get("cli-test-thread"))
            self.assertIsNotNone(saved)
            self.assertEqual([msg.role for msg in saved.messages], ["user", "assistant"])
            self.assertEqual(saved.messages[0].content, "inspect tools")
            self.assertEqual(saved.messages[1].content, "done")

            names_after = [tool.name for tool in get_all_tools(wrap=False)]
            self.assertIn("write_file", names_after)
            self.assertIn("execute_bash", names_after)

    def test_run_headless_forwards_events_to_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ThreadStore(storage_path=tmp)
            observed = []

            async def fake_handle_message(**kwargs):
                yield json.dumps({"type": "token", "content": "A"})
                yield json.dumps({"type": "token", "content": "B"})
                yield json.dumps({"type": "done"})

            with patch("app.headless_cli.thread_store", store), \
                 patch("app.headless_cli.super_agent.handle_message", fake_handle_message):
                result = asyncio.run(
                    run_headless(
                        HeadlessCLIConfig(message="hi"),
                        event_handler=lambda event: observed.append(event["type"]),
                    )
                )

            self.assertEqual(result["output"], "AB")
            self.assertEqual(observed, ["token", "token", "done"])


if __name__ == "__main__":
    unittest.main()
