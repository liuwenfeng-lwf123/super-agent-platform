from app.memory.store import memory_store
from app.models.provider import llm_provider
from langchain_core.messages import HumanMessage, SystemMessage
import json
import re

EXTRACT_SYSTEM = """You analyze conversations to extract user preferences and important context that should be remembered for future interactions.

Extract ONLY:
1. User preferences (language, style, tools, frameworks they prefer)
2. User context (their role, company, tech stack, project details)
3. Important facts the user explicitly stated about themselves
4. Recurring patterns in what the user asks for

Do NOT extract:
- Task-specific details that won't be relevant later
- Temporary context (current time, specific URLs, one-off questions)
- Information about other people

Respond ONLY in this JSON format, no other text:
{"facts": [{"key": "short_label", "value": "the fact", "category": "preference|context|pattern"}]}

If nothing worth remembering, respond with: {"facts": []}"""


async def extract_and_store_memory(
    user_message: str, assistant_response: str
) -> list[dict]:
    if len(user_message) < 10 or len(assistant_response) < 20:
        return []

    combined = f"User: {user_message[:500]}\n\nAssistant: {assistant_response[:500]}"

    try:
        chat_model = llm_provider.get_chat_model(streaming=False)
        response = await chat_model.ainvoke([
            SystemMessage(content=EXTRACT_SYSTEM),
            HumanMessage(content=combined),
        ])
        content = response.content if hasattr(response, "content") else str(response)

        json_match = content
        if "{" in content:
            start = content.index("{")
            end = content.rindex("}") + 1
            json_match = content[start:end]

        data = json.loads(json_match)
        facts = data.get("facts", [])

        stored = []
        for fact in facts[:5]:
            key = fact.get("key", "")
            value = fact.get("value", "")
            category = fact.get("category", "knowledge")
            if key and value and len(key) < 100 and len(value) < 500:
                await memory_store.add(key, value, category)
                stored.append({"key": key, "value": value, "category": category})

        return stored

    except Exception:
        return []
