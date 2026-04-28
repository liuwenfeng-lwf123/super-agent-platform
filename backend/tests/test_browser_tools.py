import json
import subprocess
import unittest
from unittest.mock import patch

from app.agents.system_tools import (
    SYSTEM_TOOLS,
    browser_click,
    browser_extract_text,
    browser_fill,
    browser_get_state,
    browser_open,
    browser_run_javascript,
)
from app.agents.tool_runtime import CATEGORY_MAP, READ_ONLY_TOOL_NAMES, SEARCH_HINTS_MAP


class TestBrowserTools(unittest.TestCase):
    def test_browser_open_uses_named_browser_on_macos(self):
        readiness_result = subprocess.CompletedProcess(
            args=["osascript"], returncode=0,
            stdout='{"url":"https://example.com","title":"Example","readyState":"complete"}\n',
            stderr="",
        )
        with patch("app.agents.system_tools.platform.system", return_value="Darwin"), \
             patch("app.agents.system_tools.subprocess.Popen") as mock_popen, \
             patch("app.agents.system_tools.subprocess.run", return_value=readiness_result), \
             patch("app.agents.system_tools.time.sleep"):
            result = browser_open.func("https://example.com", browser="chrome")

        self.assertTrue(result.startswith("Opened https://example.com in Google Chrome"))
        # Popen may be called >1 time (Playwright driver + open command); find the open -a call
        open_calls = [c for c in mock_popen.call_args_list if isinstance(c.args[0], list) and "open" in c.args[0]]
        self.assertTrue(len(open_calls) >= 1, f"Expected at least one 'open' call, got: {mock_popen.call_args_list}")
        args = open_calls[0].args[0]
        self.assertEqual(args[:3], ["open", "-a", "Google Chrome"])
        self.assertEqual(args[3], "https://example.com")

    def test_browser_get_state_returns_parsed_json(self):
        completed = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout='{"url":"https://example.com","title":"Example","readyState":"complete"}\n',
            stderr="",
        )
        with patch("app.agents.system_tools.platform.system", return_value="Darwin"), patch(
            "app.agents.system_tools.subprocess.run",
            return_value=completed,
        ) as mock_run:
            result = browser_get_state.func(browser="Safari")

        payload = json.loads(result)
        self.assertEqual(payload["browser"], "Safari")
        self.assertEqual(payload["url"], "https://example.com")
        self.assertEqual(payload["title"], "Example")
        applescript = mock_run.call_args.args[0][2]
        self.assertIn("do JavaScript", applescript)
        self.assertIn("document.readyState", applescript)

    def test_browser_click_fill_and_extract_text_use_js_bridge(self):
        responses = [
            subprocess.CompletedProcess(args=["osascript"], returncode=0, stdout='{"ok":true,"selector":"#go","text":"Go"}', stderr=""),
            subprocess.CompletedProcess(args=["osascript"], returncode=0, stdout='{"ok":true,"selector":"#query","value_length":4,"submitted":true}', stderr=""),
            subprocess.CompletedProcess(args=["osascript"], returncode=0, stdout='{"ok":true,"selector":"main","truncated":false,"text":"Hello world"}', stderr=""),
        ]
        with patch("app.agents.system_tools.platform.system", return_value="Darwin"), patch(
            "app.agents.system_tools.subprocess.run",
            side_effect=responses,
        ) as mock_run:
            click_result = browser_click.func(selector="#go", browser="chrome")
            fill_result = browser_fill.func(selector="#query", text="demo", submit=True)
            extract_result = browser_extract_text.func(selector="main", max_chars=64)

        self.assertIn('"ok":true', click_result)
        self.assertIn('"submitted":true', fill_result)
        self.assertIn('"Hello world"', extract_result)
        click_script = mock_run.call_args_list[0].args[0][2]
        fill_script = mock_run.call_args_list[1].args[0][2]
        extract_script = mock_run.call_args_list[2].args[0][2]
        self.assertIn("scrollIntoView", click_script)
        self.assertIn("requestSubmit", fill_script)
        self.assertIn("text.slice(0, maxChars)", extract_script)

    def test_browser_tools_report_platform_and_browser_errors(self):
        with patch("app.agents.system_tools.platform.system", return_value="Linux"):
            non_macos = browser_run_javascript.func(script="1+1", browser="Safari")
        self.assertIn("macOS", non_macos)

        completed = subprocess.CompletedProcess(
            args=["osascript"],
            returncode=0,
            stdout="__BROWSER_ERROR__:No browser window open\n",
            stderr="",
        )
        with patch("app.agents.system_tools.platform.system", return_value="Darwin"), patch(
            "app.agents.system_tools.subprocess.run",
            return_value=completed,
        ):
            browser_error = browser_run_javascript.func(script="1+1", browser="Safari")
            unsupported = browser_run_javascript.func(script="1+1", browser="Firefox")

        self.assertEqual(browser_error, "Browser error: No browser window open")
        self.assertIn("Unsupported browser", unsupported)

    def test_browser_tools_are_registered_for_runtime_discovery(self):
        system_tool_names = {tool.name for tool in SYSTEM_TOOLS}
        self.assertTrue({
            "browser_open",
            "browser_get_state",
            "browser_run_javascript",
            "browser_click",
            "browser_fill",
            "browser_extract_text",
        }.issubset(system_tool_names))
        self.assertEqual(CATEGORY_MAP["browser_click"], "system")
        self.assertIn("browser automation", SEARCH_HINTS_MAP["browser_run_javascript"])
        self.assertIn("browser_get_state", READ_ONLY_TOOL_NAMES)
        self.assertIn("browser_extract_text", READ_ONLY_TOOL_NAMES)
        self.assertNotIn("browser_click", READ_ONLY_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
