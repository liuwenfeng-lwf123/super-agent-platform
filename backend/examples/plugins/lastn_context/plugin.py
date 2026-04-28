"""Demo ContextEngine: keep last N pairs.

Replaces the default message-assembly with a very aggressive trim: system
prompt + last `keep_pairs * 2` messages + the new user message. Useful as a
baseline for latency-sensitive sessions.
"""
from __future__ import annotations


class LastNContextEngine:
    def __init__(self, keep_pairs: int = 4):
        self.keep_pairs = max(1, int(keep_pairs))

    async def build_context(
        self,
        system_prompt: str,
        history: list[dict],
        new_message: str,
    ) -> list[dict]:
        keep = self.keep_pairs * 2
        trimmed = history[-keep:] if len(history) > keep else list(history)
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(trimmed)
        messages.append({"role": "user", "content": new_message})
        return messages
