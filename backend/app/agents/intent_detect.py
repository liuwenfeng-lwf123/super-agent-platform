"""
Shared intent-detection helpers for user messages.

Used by both super_agent.py and local/agent.py to avoid duplicated
keyword-matching logic for screenshots, search, browser open, etc.
"""
import re


def extract_direct_search_query(message: str) -> str | None:
    text = message.strip()
    lowered = text.lower()
    prefixes = ["搜索一下", "搜索", "帮我搜索一下", "帮我搜索", "查一下", "帮我查一下", "搜一下", "搜", "search ", "google "]
    for prefix in prefixes:
        if lowered.startswith(prefix.lower()):
            query = text[len(prefix):].strip(" ：:，,。`'\"")
            return query or None
    return None


def message_requests_screenshot(message: str) -> bool:
    lowered = message.lower()
    negative_triggers = ["不要截图", "别截图", "不用截图", "不要截屏", "别截屏", "do not screenshot", "don't screenshot", "no screenshot"]
    if any(trigger in lowered or trigger in message for trigger in negative_triggers):
        return False
    return any(trigger in message for trigger in ["截图", "截屏", "截个屏", "截个", "的图", "的截图", "的截屏"]) or "screenshot" in lowered


def extract_direct_browser_screenshot_url(message: str) -> str | None:
    text = message.strip()
    if not message_requests_screenshot(text):
        return None
    if not any(trigger in text for trigger in ["打开", "访问", "浏览", "open", "visit", "go to"]):
        return None
    match = re.search(r"https?://[^\s，。；;、]+", text)
    if match:
        return match.group(0).rstrip("，。；;、")
    return None


def extract_direct_browser_open_url(message: str) -> str | None:
    text = message.strip()
    if message_requests_screenshot(text):
        return None
    if not any(trigger in text for trigger in ["打开", "访问", "浏览", "open", "visit", "go to"]):
        return None
    action_words = ["点击", "输入", "填写", "登录", "滚动", "选择", "提交", "click", "fill", "type", "login", "scroll", "submit"]
    lowered = text.lower()
    if any(word in lowered or word in text for word in action_words):
        return None
    match = re.search(r"https?://[^\s，。；;、]+", text)
    if match:
        return match.group(0).rstrip("，。；;、")
    return None


def is_simple_open_screenshot_request(message: str) -> bool:
    if not message_requests_screenshot(message):
        return False
    action_words = ["点击", "输入", "搜索", "填写", "登录", "滚动", "选择", "提交", "click", "fill", "type", "login", "scroll", "search", "submit"]
    lowered = message.lower()
    return not any(word in lowered or word in message for word in action_words)


def is_standalone_screenshot_request(message: str) -> bool:
    if not message_requests_screenshot(message):
        return False
    text = message.strip()
    lowered = text.lower()
    if re.search(r"https?://", text):
        return False
    action_words = ["打开", "访问", "浏览", "点击", "输入", "搜索", "填写", "登录", "滚动", "选择", "提交", "open", "visit", "go to", "click", "fill", "type", "login", "scroll", "search", "submit"]
    return not any(word in lowered or word in text for word in action_words)


def screenshot_target_for_message(message: str) -> str:
    lowered = message.lower()
    if any(trigger in message for trigger in ["桌面", "电脑", "屏幕", "显示器"]) or any(trigger in lowered for trigger in ["desktop", "computer", "screen", "monitor", "display"]):
        return "screen"
    return "browser"
