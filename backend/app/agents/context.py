import os
import json
import logging
from dataclasses import dataclass, field
from langchain_core.messages import HumanMessage, SystemMessage
from app.models.provider import llm_provider
from app.agents.cost_tracker import estimate_tokens

logger = logging.getLogger(__name__)

SUMMARIZE_SYSTEM = """Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary must be thorough in capturing technical details, code patterns, and decisions essential for continuing work without losing context.

Before providing your final summary, organize your analysis:
1. Chronologically analyze each section — identify user requests, your approach, key decisions, specific file names, code snippets, function signatures, edits made, errors encountered and how they were fixed.
2. Pay special attention to user feedback — corrections, confirmations, changed requirements.

Your summary MUST include these sections:
1. **Primary Request and Intent**: All user requests and intents in detail
2. **Key Technical Concepts**: Technologies, frameworks, patterns discussed
3. **Files and Code Sections**: Specific files examined/modified/created with code snippets and why
4. **Errors and Fixes**: All errors encountered and how they were resolved
5. **Problem Solving**: Problems solved and ongoing troubleshooting
6. **All User Messages**: List ALL user messages (critical for understanding feedback)
7. **Pending Tasks**: Any tasks explicitly requested but not yet completed
8. **Current Work**: Precisely what was being worked on immediately before this summary, with file names and code snippets

Write the summary in the same language as the conversation. Be factual and thorough."""

# --- Tool Result Micro-Compaction ---
TOOL_RESULT_MAX_TOKENS = 3000  # Max tokens per tool result before compaction
COMPACTABLE_TOOLS = {"read_file", "execute_bash", "execute_python", "web_fetch", "web_search", "list_files"}
TOOL_RESULT_CLEARED_MESSAGE = "[Tool result content cleared — exceeded token limit]"


def micro_compact_tool_result(tool_name: str, content: str) -> str:
    """Compact large tool results to save context window space.
    Inspired by Claude Code's microCompact — truncates oversized tool outputs."""
    if tool_name not in COMPACTABLE_TOOLS:
        return content
    tokens = estimate_tokens(content)
    if tokens <= TOOL_RESULT_MAX_TOKENS:
        return content
    # Keep first and last portions
    char_limit = TOOL_RESULT_MAX_TOKENS * 3  # ~3 chars per token
    half = char_limit // 2
    return content[:half] + f"\n\n... [{tokens - TOOL_RESULT_MAX_TOKENS} tokens truncated] ...\n\n" + content[-half:]

# --- Auto-Compact Configuration (inspired by Claude Code) ---
CONTEXT_WINDOW_TOKENS = 128_000        # Model context window
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 4000   # Reserved for compaction summary output
AUTOCOMPACT_BUFFER_TOKENS = 13_000     # Trigger compaction at this margin
WARNING_THRESHOLD_BUFFER = 20_000      # Show warning at this margin
ERROR_THRESHOLD_BUFFER = 8_000         # Emergency at this margin
MAX_CONSECUTIVE_COMPACT_FAILURES = 3   # Circuit breaker


@dataclass
class CompactState:
    """Tracks auto-compaction state across a session."""
    consecutive_failures: int = 0
    total_compactions: int = 0
    last_compacted_tokens: int = 0
    is_disabled: bool = False
    compact_boundary: int = 0          # index: messages before this were already compacted
    last_summary: str = ""             # cached summary from last compaction


_compact_state = CompactState()


def estimate_message_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        total += estimate_tokens(content) if content else 0
        total += 4  # Per-message overhead
    return total


def calculate_token_warning_state(token_usage: int) -> dict:
    """Calculate context window usage and warning levels."""
    effective_window = CONTEXT_WINDOW_TOKENS - MAX_OUTPUT_TOKENS_FOR_SUMMARY
    compact_threshold = effective_window - AUTOCOMPACT_BUFFER_TOKENS
    warning_threshold = effective_window - WARNING_THRESHOLD_BUFFER
    error_threshold = effective_window - ERROR_THRESHOLD_BUFFER

    percent_left = max(0, round(((effective_window - token_usage) / effective_window) * 100))

    return {
        "token_usage": token_usage,
        "effective_window": effective_window,
        "compact_threshold": compact_threshold,
        "percent_left": percent_left,
        "needs_compaction": token_usage >= compact_threshold and not _compact_state.is_disabled,
        "is_warning": token_usage >= warning_threshold,
        "is_error": token_usage >= error_threshold,
        "compaction_disabled": _compact_state.is_disabled,
        "total_compactions": _compact_state.total_compactions,
    }


async def summarize_messages(messages: list[dict], max_tokens: int = 500) -> str:
    if not messages:
        return ""

    conversation_text = ""
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if len(content) > 500:
            content = content[:500] + "..."
        conversation_text += f"[{role}]: {content}\n\n"

    if len(conversation_text) < 2000:
        return conversation_text

    try:
        chat_model = llm_provider.get_chat_model(streaming=False)
        response = await chat_model.ainvoke([
            SystemMessage(content=SUMMARIZE_SYSTEM),
            HumanMessage(content=f"Summarize this conversation:\n\n{conversation_text}"),
        ])
        summary = response.content if hasattr(response, "content") else str(response)
        return summary[:max_tokens * 2]
    except Exception as e:
        logger.debug("Suppressed error in context: %s", e)
        parts = []
        for msg in messages:
            content = msg.get("content", "")
            if content:
                parts.append(f"{msg.get('role', '?')}: {content[:200]}")
        return "\n".join(parts)[:2000]


async def auto_compact(messages: list[dict]) -> tuple[str | None, str]:
    """Multi-level auto-compaction. Returns (summary, level) or (None, 'none').

    Levels:
    - 'none': no compaction needed
    - 'auto': standard compaction (summarize older messages)
    - 'micro': aggressive (summarize everything except last 2 messages)
    - 'emergency': ultra-aggressive (hard truncate + summarize)

    Uses compact_boundary to avoid re-summarizing already compacted messages.
    """
    global _compact_state

    if _compact_state.is_disabled:
        return None, "none"

    token_usage = estimate_message_tokens(messages)
    state = calculate_token_warning_state(token_usage)

    if not state["needs_compaction"]:
        return None, "none"

    # Determine compaction level
    if state["is_error"]:
        level = "emergency"
    elif token_usage > state["compact_threshold"] + AUTOCOMPACT_BUFFER_TOKENS:
        level = "micro"
    else:
        level = "auto"

    # Only summarize messages after compact_boundary (skip already-compacted ones)
    boundary = min(_compact_state.compact_boundary, len(messages))
    uncompacted = messages[boundary:]

    logger.info(f"Auto-compact triggered: level={level}, tokens={token_usage}, boundary={boundary}, uncompacted={len(uncompacted)}")

    def _update_state(new_boundary: int, summary: str):
        _compact_state.consecutive_failures = 0
        _compact_state.total_compactions += 1
        _compact_state.last_compacted_tokens = token_usage
        _compact_state.compact_boundary = new_boundary
        _compact_state.last_summary = summary

    try:
        if level == "emergency":
            # Hard truncate: keep only last 2 messages, summarize rest
            if len(uncompacted) > 4:
                to_summarize = uncompacted[:-2]
                prefix = f"[Previous summary]\n{_compact_state.last_summary}\n\n" if _compact_state.last_summary else ""
                summary = prefix + await _fast_summarize(to_summarize)
                _update_state(len(messages) - 2, summary)
                return summary, level
        elif level == "micro":
            # Keep last 3 messages, summarize rest
            if len(uncompacted) > 6:
                to_summarize = uncompacted[:-3]
                prefix = f"[Previous summary]\n{_compact_state.last_summary}\n\n" if _compact_state.last_summary else ""
                summary = prefix + await summarize_messages(to_summarize, max_tokens=800)
                _update_state(len(messages) - 3, summary)
                return summary, level
        else:
            # Standard: summarize uncompacted portion
            prefix = f"[Previous summary]\n{_compact_state.last_summary}\n\n" if _compact_state.last_summary else ""
            summary = prefix + await summarize_messages(uncompacted, max_tokens=500)
            _update_state(len(messages), summary)
            return summary, level

    except Exception as e:
        _compact_state.consecutive_failures += 1
        logger.warning(f"Auto-compact failed ({_compact_state.consecutive_failures}/{MAX_CONSECUTIVE_COMPACT_FAILURES}): {e}")
        if _compact_state.consecutive_failures >= MAX_CONSECUTIVE_COMPACT_FAILURES:
            _compact_state.is_disabled = True
            logger.error("Auto-compact circuit breaker tripped — disabled for this session")

    return None, "none"


async def _fast_summarize(messages: list[dict]) -> str:
    """Emergency fast summarization: extract key points only."""
    key_points = []
    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "")
        if role == "assistant" and content:
            # Extract first sentence only
            first_line = content.split("\n")[0][:200]
            if first_line.strip():
                key_points.append(first_line.strip())
        elif role == "user" and content:
            key_points.append(f"User: {content[:100]}")

    if len(key_points) > 20:
        key_points = key_points[-20:]

    return "[Compacted conversation summary]\n" + "\n".join(f"- {p}" for p in key_points)


def should_summarize(messages: list[dict], threshold: int = 20) -> bool:
    """Check if messages need summarization (by count or token estimate)."""
    if len(messages) > threshold:
        return True
    # Also trigger on token count
    token_est = estimate_message_tokens(messages)
    return token_est > 8000


def get_messages_for_context(
    messages: list[dict],
    summary: str | None = None,
    recent_count: int = 6,
) -> list[dict]:
    if not should_summarize(messages, recent_count * 2):
        return messages

    older = messages[:-recent_count]
    recent = messages[-recent_count:]

    context_messages = []
    if summary:
        context_messages.append({
            "role": "system",
            "content": f"[Previous conversation summary]\n{summary}",
        })

    context_messages.extend(recent)
    return context_messages


def get_compact_state() -> dict:
    """Return current compaction state for API/debug."""
    return {
        "consecutive_failures": _compact_state.consecutive_failures,
        "total_compactions": _compact_state.total_compactions,
        "last_compacted_tokens": _compact_state.last_compacted_tokens,
        "is_disabled": _compact_state.is_disabled,
        "compact_boundary": _compact_state.compact_boundary,
        "has_prior_summary": bool(_compact_state.last_summary),
    }


def reset_compact_state():
    """Reset compaction state (e.g., on new conversation)."""
    global _compact_state
    _compact_state = CompactState()
