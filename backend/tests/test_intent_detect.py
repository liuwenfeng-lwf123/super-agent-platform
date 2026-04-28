"""Unit tests for app.agents.intent_detect — shared intent detection helpers."""
import unittest

from app.agents.intent_detect import (
    extract_direct_search_query,
    message_requests_screenshot,
    extract_direct_browser_screenshot_url,
    extract_direct_browser_open_url,
    is_simple_open_screenshot_request,
    is_standalone_screenshot_request,
    screenshot_target_for_message,
)


class TestExtractDirectSearchQuery(unittest.TestCase):
    def test_chinese_prefix(self):
        self.assertEqual(extract_direct_search_query("搜索一下 Python教程"), "Python教程")

    def test_chinese_prefix_sou(self):
        self.assertEqual(extract_direct_search_query("搜 天气预报"), "天气预报")

    def test_english_prefix(self):
        self.assertEqual(extract_direct_search_query("search fastapi docs"), "fastapi docs")

    def test_google_prefix(self):
        self.assertEqual(extract_direct_search_query("google openai api"), "openai api")

    def test_no_match(self):
        self.assertIsNone(extract_direct_search_query("hello world"))

    def test_empty_query_after_prefix(self):
        self.assertIsNone(extract_direct_search_query("搜索一下"))

    def test_strips_punctuation(self):
        self.assertEqual(extract_direct_search_query("搜索一下：Python"), "Python")


class TestMessageRequestsScreenshot(unittest.TestCase):
    def test_positive_chinese(self):
        self.assertTrue(message_requests_screenshot("帮我截图"))

    def test_positive_english(self):
        self.assertTrue(message_requests_screenshot("take a screenshot please"))

    def test_negative_trigger(self):
        self.assertFalse(message_requests_screenshot("不要截图"))
        self.assertFalse(message_requests_screenshot("别截屏"))
        self.assertFalse(message_requests_screenshot("do not screenshot"))

    def test_no_trigger(self):
        self.assertFalse(message_requests_screenshot("你好"))

    def test_screenshot_in_english(self):
        self.assertTrue(message_requests_screenshot("screenshot this page"))


class TestExtractDirectBrowserScreenshotUrl(unittest.TestCase):
    def test_with_url_and_screenshot(self):
        url = extract_direct_browser_screenshot_url("打开 https://example.com 截图")
        self.assertEqual(url, "https://example.com")

    def test_no_screenshot_trigger(self):
        self.assertIsNone(extract_direct_browser_screenshot_url("打开 https://example.com"))

    def test_no_open_trigger(self):
        self.assertIsNone(extract_direct_browser_screenshot_url("截图 https://example.com"))

    def test_no_url(self):
        self.assertIsNone(extract_direct_browser_screenshot_url("打开百度截图"))


class TestExtractDirectBrowserOpenUrl(unittest.TestCase):
    def test_simple_open(self):
        self.assertEqual(
            extract_direct_browser_open_url("打开 https://google.com"),
            "https://google.com",
        )

    def test_english_visit(self):
        self.assertEqual(
            extract_direct_browser_open_url("visit https://example.org"),
            "https://example.org",
        )

    def test_returns_none_for_screenshot(self):
        self.assertIsNone(extract_direct_browser_open_url("打开 https://x.com 截图"))

    def test_returns_none_for_action(self):
        self.assertIsNone(extract_direct_browser_open_url("打开 https://x.com 并登录"))

    def test_no_open_trigger(self):
        self.assertIsNone(extract_direct_browser_open_url("https://x.com"))


class TestIsSimpleOpenScreenshotRequest(unittest.TestCase):
    def test_simple_screenshot(self):
        self.assertTrue(is_simple_open_screenshot_request("截图"))

    def test_with_action_word(self):
        self.assertFalse(is_simple_open_screenshot_request("截图并点击按钮"))

    def test_no_screenshot(self):
        self.assertFalse(is_simple_open_screenshot_request("你好"))


class TestIsStandaloneScreenshotRequest(unittest.TestCase):
    def test_standalone(self):
        self.assertTrue(is_standalone_screenshot_request("截图"))

    def test_with_url(self):
        self.assertFalse(is_standalone_screenshot_request("打开 https://example.com 截图"))

    def test_with_action(self):
        self.assertFalse(is_standalone_screenshot_request("打开百度截图"))


class TestScreenshotTargetForMessage(unittest.TestCase):
    def test_desktop(self):
        self.assertEqual(screenshot_target_for_message("桌面截图"), "screen")

    def test_computer(self):
        self.assertEqual(screenshot_target_for_message("电脑截图"), "screen")

    def test_screen_english(self):
        self.assertEqual(screenshot_target_for_message("screenshot the desktop"), "screen")

    def test_default_browser(self):
        self.assertEqual(screenshot_target_for_message("截个图"), "browser")


if __name__ == "__main__":
    unittest.main()
