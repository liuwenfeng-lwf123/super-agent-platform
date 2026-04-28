"""Tests for _shared_flow refactoring — verify standard and pro flows
delegate correctly and that the shared flow signature works."""
import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import asyncio
import json

from app.agents.super_agent import SuperAgent


class TestSharedFlowSignature(unittest.TestCase):
    """Verify _shared_flow exists and has the right parameters."""

    def test_shared_flow_is_async_generator(self):
        import inspect
        sa = SuperAgent()
        self.assertTrue(inspect.isasyncgenfunction(sa._shared_flow))

    def test_standard_flow_is_async_generator(self):
        import inspect
        sa = SuperAgent()
        self.assertTrue(inspect.isasyncgenfunction(sa._standard_flow))

    def test_pro_flow_is_async_generator(self):
        import inspect
        sa = SuperAgent()
        self.assertTrue(inspect.isasyncgenfunction(sa._pro_flow))

    def test_shared_flow_parameters(self):
        import inspect
        sig = inspect.signature(SuperAgent._shared_flow)
        param_names = list(sig.parameters.keys())
        self.assertIn("flow_name", param_names)
        self.assertIn("base_prompt", param_names)
        self.assertIn("use_memory_provider", param_names)
        self.assertIn("use_frozen_memory", param_names)
        self.assertIn("enable_screenshot_fallback", param_names)
        self.assertIn("enable_session_search", param_names)


class TestStandardFlowDelegation(unittest.TestCase):
    """Verify _standard_flow passes correct flags to _shared_flow."""

    def test_standard_passes_correct_flags(self):
        sa = SuperAgent()
        called_kwargs = {}

        async def mock_shared_flow(self_arg, *args, **kwargs):
            called_kwargs.update(kwargs)
            # Must be an async generator
            return
            yield  # noqa: makes this an async gen

        with patch.object(SuperAgent, "_shared_flow", mock_shared_flow):
            async def run():
                async for _ in sa._standard_flow("hello", [], None):
                    pass
            asyncio.run(run())

        self.assertTrue(called_kwargs.get("use_memory_provider"))
        self.assertTrue(called_kwargs.get("use_frozen_memory"))
        self.assertTrue(called_kwargs.get("enable_screenshot_fallback"))
        self.assertTrue(called_kwargs.get("enable_session_search"))


class TestProFlowDelegation(unittest.TestCase):
    """Verify _pro_flow passes correct flags to _shared_flow."""

    def test_pro_passes_correct_flags(self):
        sa = SuperAgent()
        called_kwargs = {}

        async def mock_shared_flow(self_arg, *args, **kwargs):
            called_kwargs.update(kwargs)
            return
            yield  # noqa

        with patch.object(SuperAgent, "_shared_flow", mock_shared_flow):
            async def run():
                async for _ in sa._pro_flow("hello", [], None):
                    pass
            asyncio.run(run())

        self.assertFalse(called_kwargs.get("use_memory_provider"))
        self.assertFalse(called_kwargs.get("use_frozen_memory"))
        self.assertFalse(called_kwargs.get("enable_screenshot_fallback"))
        self.assertFalse(called_kwargs.get("enable_session_search"))


class TestToolErrorTracking(unittest.TestCase):
    """Verify _run_agent_loop tracks tool errors in on_tool_end events."""

    def test_tool_error_yields_error_status(self):
        sa = SuperAgent()

        # Fake agent that produces on_tool_start + on_tool_end with error
        async def fake_astream_events(messages, version="v2"):
            yield {"event": "on_tool_start", "name": "bash", "data": {"input": {"cmd": "ls"}}}
            yield {"event": "on_tool_end", "name": "bash", "data": {"output": "Error: command not found"}}

        mock_agent = MagicMock()
        mock_agent.astream_events = fake_astream_events

        mock_chat_model = MagicMock()

        with patch("app.agents.super_agent.create_react_agent", return_value=mock_agent), \
             patch("app.agents.super_agent.llm_provider") as mock_llm, \
             patch("app.agents.super_agent.cost_tracker") as mock_cost:
            mock_llm.get_chat_model.return_value = mock_chat_model
            mock_llm.get_fallback_model_names.return_value = ["test-model"]
            mock_llm.aclose_model = AsyncMock()
            mock_cost.has_active_tracking.return_value = False
            mock_cost.current_input_tokens.return_value = 0

            events = []
            async def run():
                async for ev in sa._run_agent_loop([], "sys", "msg", "test-model", [], None, None, "standard"):
                    events.append(json.loads(ev))
            asyncio.run(run())

        tool_results = [e for e in events if e.get("type") == "tool_result"]
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(tool_results[0]["data"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
