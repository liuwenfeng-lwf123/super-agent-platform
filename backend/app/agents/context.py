import os
import json
from langchain_core.messages import HumanMessage, SystemMessage
from app.models.provider import llm_provider

SUMMARIZE_SYSTEM = """You are a conversation summarizer. Given a conversation history, produce a concise summary that:
1. Captures the key topics discussed
2. Notes any important decisions, results, or conclusions
3. Preserves specific facts, numbers, or references that were established
4. Is much shorter than the original conversation

Write the summary in the same language as the conversation. Be factual and concise."""


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
    except Exception:
        parts = []
        for msg in messages:
            content = msg.get("content", "")
            if content:
                parts.append(f"{msg.get('role', '?')}: {content[:200]}")
        return "\n".join(parts)[:2000]


def should_summarize(messages: list[dict], threshold: int = 20) -> bool:
    return len(messages) > threshold


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
