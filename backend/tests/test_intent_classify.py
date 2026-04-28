"""Tests for LLM-backed intent classification with rule-based fallback."""
import asyncio
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

from app.agents.intent_classify import (
    classify_intent,
    _rule_based_classify,
    _parse_llm_response,
)


def _run(coro):
    return asyncio.run(coro)


class TestRuleBasedClassify(unittest.TestCase):
    def test_search(self):
        result = _rule_based_classify("搜索一下 Python教程")
        self.assertEqual(result["intent"], "search")
        self.assertEqual(result["query"], "Python教程")
        self.assertGreaterEqual(result["confidence"], 0.9)

    def test_browser_screenshot(self):
        result = _rule_based_classify("打开 https://example.com 截图")
        self.assertEqual(result["intent"], "browser_screenshot")
        self.assertEqual(result["url"], "https://example.com")

    def test_standalone_screenshot(self):
        result = _rule_based_classify("截图")
        self.assertEqual(result["intent"], "screenshot")
        self.assertEqual(result["target"], "browser")

    def test_desktop_screenshot(self):
        result = _rule_based_classify("桌面截图")
        self.assertEqual(result["intent"], "screenshot")
        self.assertEqual(result["target"], "screen")

    def test_browser_open(self):
        result = _rule_based_classify("打开 https://google.com")
        self.assertEqual(result["intent"], "browser_open")
        self.assertEqual(result["url"], "https://google.com")

    def test_ambiguous_returns_none(self):
        result = _rule_based_classify("你好世界")
        self.assertIsNone(result)

    def test_screenshot_with_context(self):
        # Has screenshot keyword, no URL — classified as standalone screenshot
        result = _rule_based_classify("截屏那个页面的内容")
        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], "screenshot")


class TestParseResponse(unittest.TestCase):
    def test_valid_json(self):
        resp = '{"intent": "search", "query": "hello", "url": null, "target": null, "confidence": 0.9}'
        result = _parse_llm_response(resp)
        self.assertEqual(result["intent"], "search")
        self.assertEqual(result["query"], "hello")

    def test_markdown_code_block(self):
        resp = '```json\n{"intent": "chat"}\n```'
        result = _parse_llm_response(resp)
        self.assertEqual(result["intent"], "chat")

    def test_invalid_intent_normalized(self):
        resp = '{"intent": "unknown_intent", "confidence": 0.5}'
        result = _parse_llm_response(resp)
        self.assertEqual(result["intent"], "chat")

    def test_invalid_json(self):
        result = _parse_llm_response("not json at all")
        self.assertIsNone(result)

    def test_missing_intent_key(self):
        result = _parse_llm_response('{"action": "search"}')
        self.assertIsNone(result)


class TestClassifyIntent(unittest.TestCase):
    def test_fast_path_search(self):
        result = _run(classify_intent("搜索 Python"))
        self.assertEqual(result["intent"], "search")
        # Should be high confidence (fast path)
        self.assertGreaterEqual(result["confidence"], 0.9)

    def test_fast_path_screenshot(self):
        result = _run(classify_intent("截图"))
        self.assertEqual(result["intent"], "screenshot")

    def test_ambiguous_falls_back_to_chat(self):
        # Mock LLM to fail so we get the fallback
        with patch("app.agents.intent_classify._llm_classify", new_callable=AsyncMock, return_value=None):
            result = _run(classify_intent("你好"))
        self.assertEqual(result["intent"], "chat")

    def test_llm_timeout_handled(self):
        async def slow_classify(msg):
            await asyncio.sleep(10)
            return {"intent": "chat"}

        with patch("app.agents.intent_classify._llm_classify", side_effect=slow_classify):
            result = _run(classify_intent("复杂请求", timeout=0.01))
        # Should return something (fallback), not raise
        self.assertIn("intent", result)

    def test_llm_result_used_for_ambiguous(self):
        llm_result = {"intent": "browser_open", "url": "https://x.com", "query": None, "target": None, "confidence": 0.85}
        with patch("app.agents.intent_classify._llm_classify", new_callable=AsyncMock, return_value=llm_result):
            result = _run(classify_intent("帮我看看 x.com"))
        self.assertEqual(result["intent"], "browser_open")


if __name__ == "__main__":
    unittest.main()
