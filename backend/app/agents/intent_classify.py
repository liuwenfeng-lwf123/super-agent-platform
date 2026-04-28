"""
LLM-backed intent classification with hardcoded fallback.

Falls back to the rule-based intent_detect.py when:
  - The LLM provider is unavailable
  - The message clearly matches a hardcoded pattern (fast path)
  - The classification call fails or times out

Usage:
    from app.agents.intent_classify import classify_intent
    result = await classify_intent("打开 https://example.com 截图")
    # result: {"intent": "browser_screenshot", "url": "https://example.com", "confidence": 0.95}
"""
import asyncio
import json
import logging
from typing import Optional

from app.agents.intent_detect import (
    extract_direct_search_query,
    message_requests_screenshot,
    extract_direct_browser_screenshot_url,
    extract_direct_browser_open_url,
    is_standalone_screenshot_request,
    screenshot_target_for_message,
)

logger = logging.getLogger(__name__)

# Fast-path: if rule-based detection gives a clear result, skip LLM entirely.
# This avoids latency for obvious cases.

_CLASSIFY_SYSTEM = """You are a fast intent classifier. Given a user message, output JSON with:
- intent: one of "search", "screenshot", "browser_screenshot", "browser_open", "chat"
- url: extracted URL if any (null otherwise)
- query: search query if intent is "search" (null otherwise)  
- target: "screen" or "browser" if intent involves screenshot (null otherwise)
- confidence: 0.0-1.0

Rules:
- "search": user wants to search the web
- "screenshot": user wants a screenshot of current screen/browser (no URL)
- "browser_screenshot": user wants to open a URL AND take a screenshot
- "browser_open": user wants to open a URL without screenshot
- "chat": general conversation, no special action needed

Output ONLY valid JSON, no explanation."""


async def classify_intent(message: str, timeout: float = 3.0) -> dict:
    """Classify user intent. Uses fast-path rules first, LLM as fallback for ambiguous cases.

    Returns dict with keys: intent, url, query, target, confidence
    """
    # --- Fast path: rule-based detection ---
    result = _rule_based_classify(message)
    if result and result.get("confidence", 0) >= 0.9:
        return result

    # --- LLM classification for ambiguous messages ---
    try:
        llm_result = await asyncio.wait_for(_llm_classify(message), timeout=timeout)
        if llm_result:
            return llm_result
    except asyncio.TimeoutError:
        logger.debug("Intent LLM classification timed out for: %.50s", message)
    except Exception as e:
        logger.debug("Intent LLM classification failed: %s", e)

    # --- Fallback: return rule-based result or default to chat ---
    return result or {"intent": "chat", "url": None, "query": None, "target": None, "confidence": 0.5}


def _rule_based_classify(message: str) -> Optional[dict]:
    """Rule-based fast classification. Returns None if ambiguous."""
    # Search
    query = extract_direct_search_query(message)
    if query:
        return {"intent": "search", "url": None, "query": query, "target": None, "confidence": 0.95}

    # Browser screenshot (open URL + screenshot)
    url = extract_direct_browser_screenshot_url(message)
    if url:
        return {"intent": "browser_screenshot", "url": url, "query": None, "target": "browser", "confidence": 0.95}

    # Standalone screenshot (no URL)
    if is_standalone_screenshot_request(message):
        target = screenshot_target_for_message(message)
        return {"intent": "screenshot", "url": None, "query": None, "target": target, "confidence": 0.95}

    # Browser open (URL without screenshot)
    url = extract_direct_browser_open_url(message)
    if url:
        return {"intent": "browser_open", "url": url, "query": None, "target": None, "confidence": 0.95}

    # Screenshot detected but with URL (ambiguous, let LLM decide)
    if message_requests_screenshot(message):
        return {"intent": "screenshot", "url": None, "query": None, "target": screenshot_target_for_message(message), "confidence": 0.7}

    # No clear intent detected
    return None


async def _llm_classify(message: str) -> Optional[dict]:
    """Use LLM for intent classification of ambiguous messages."""
    try:
        from app.models.provider import llm_provider
        from langchain_core.messages import SystemMessage, HumanMessage

        # Use first available model (cheapest fallback)
        candidates = llm_provider.get_fallback_model_names(None)
        if not candidates:
            return None
        fast_model = candidates[0]
        chat_model = llm_provider.get_chat_model(fast_model, streaming=False)
        try:
            messages = [
                SystemMessage(content=_CLASSIFY_SYSTEM),
                HumanMessage(content=message),
            ]
            response = await chat_model.ainvoke(messages)
            content = response.content if hasattr(response, "content") else str(response)
            # Parse JSON from response
            parsed = _parse_llm_response(content)
            if parsed:
                return parsed
        finally:
            await llm_provider.aclose_model(chat_model)
    except Exception as e:
        logger.debug("LLM intent classify unavailable: %s", e)
    return None


def _parse_llm_response(content: str) -> Optional[dict]:
    """Extract JSON from LLM response, handling markdown code blocks."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        content = "\n".join(lines)
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "intent" in data:
            # Normalize
            valid_intents = {"search", "screenshot", "browser_screenshot", "browser_open", "chat"}
            if data["intent"] not in valid_intents:
                data["intent"] = "chat"
            data.setdefault("url", None)
            data.setdefault("query", None)
            data.setdefault("target", None)
            data.setdefault("confidence", 0.8)
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None
