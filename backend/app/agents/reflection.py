"""
Agent Reflection — self-verification and correction loop.

After the main agent loop produces output, a lightweight reflection step
evaluates the response quality and triggers a correction cycle if needed.
Maximum 3 reflection rounds to prevent infinite loops.
"""
import json
import logging
from typing import AsyncGenerator, Optional
from langchain_core.messages import SystemMessage, HumanMessage
from app.models.provider import llm_provider
from app.agents.cost_tracker import cost_tracker, estimate_tokens

logger = logging.getLogger(__name__)

MAX_REFLECTION_ROUNDS = 3

REFLECTION_SYSTEM = """You are a strict quality reviewer. Evaluate whether an AI assistant's response fully and correctly addresses the user's request.

Scoring criteria:
1. **Completeness** — Does it answer ALL parts of the question?
2. **Accuracy** — Are facts, code, or reasoning correct?
3. **Actionability** — Can the user directly use this response?
4. **Coherence** — Is it well-structured and clear?

Respond ONLY in JSON:
{
  "pass": true/false,
  "score": 0-100,
  "issues": ["list of specific problems found"],
  "suggestion": "how to fix the response (empty if pass=true)"
}

Be strict: if ANY part is wrong, incomplete, or misleading, set pass=false.
Only set pass=true if the response is genuinely good."""

CORRECTION_SYSTEM = """You are correcting a previous AI response based on reviewer feedback.

Rules:
1. Fix ALL identified issues
2. Keep the parts that were already correct
3. Produce a COMPLETE corrected response (not a diff)
4. Respond in the same language as the original"""


class ReflectionResult:
    """Outcome of a reflection cycle."""
    __slots__ = ("passed", "score", "issues", "rounds_used", "original", "final")

    def __init__(self):
        self.passed: bool = False
        self.score: int = 0
        self.issues: list[str] = []
        self.rounds_used: int = 0
        self.original: str = ""
        self.final: str = ""


async def evaluate_response(
    user_message: str,
    assistant_response: str,
    model: str | None = None,
) -> dict:
    """Run a single reflection evaluation. Returns parsed JSON or fallback."""
    eval_prompt = (
        f"User request:\n{user_message}\n\n"
        f"Assistant response:\n{assistant_response[:4000]}\n\n"
        "Evaluate this response."
    )
    try:
        chat_model = llm_provider.get_chat_model(model, streaming=False)
        try:
            response = await chat_model.ainvoke([
                SystemMessage(content=REFLECTION_SYSTEM),
                HumanMessage(content=eval_prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)
            cost_tracker.add_tokens(
                input_tokens=estimate_tokens(REFLECTION_SYSTEM + eval_prompt),
                output_tokens=estimate_tokens(content),
            )
            # Parse JSON from response
            if "{" in content:
                start = content.index("{")
                end = content.rindex("}") + 1
                return json.loads(content[start:end])
        finally:
            await llm_provider.aclose_model(chat_model)
    except Exception as e:
        logger.warning("Reflection evaluation failed: %s", e)

    # Fallback: assume pass to avoid blocking
    return {"pass": True, "score": 70, "issues": [], "suggestion": ""}


async def correct_response(
    user_message: str,
    original_response: str,
    issues: list[str],
    suggestion: str,
    model: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream a corrected response based on reflection feedback."""
    correction_prompt = (
        f"Original user request:\n{user_message}\n\n"
        f"Previous response:\n{original_response[:3000]}\n\n"
        f"Issues found:\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
        f"Reviewer suggestion: {suggestion}\n\n"
        "Provide the complete corrected response."
    )
    chat_model = llm_provider.get_chat_model(model, streaming=True)
    try:
        async for chunk in chat_model.astream([
            SystemMessage(content=CORRECTION_SYSTEM),
            HumanMessage(content=correction_prompt),
        ]):
            if hasattr(chunk, "content") and chunk.content:
                yield chunk.content
    except Exception as e:
        logger.warning("Reflection correction failed: %s", e)
        yield f"\n\n(Correction failed: {e})"
    finally:
        await llm_provider.aclose_model(chat_model)


async def reflect_and_correct(
    user_message: str,
    assistant_response: str,
    model: str | None = None,
    max_rounds: int = MAX_REFLECTION_ROUNDS,
    min_score: int = 75,
) -> AsyncGenerator[dict, None]:
    """
    Full reflection loop. Yields events:
      {"type": "reflection_start", "round": N}
      {"type": "reflection_eval", "round": N, "score": ..., "pass": ..., "issues": [...]}
      {"type": "reflection_correction_token", "content": "..."}
      {"type": "reflection_done", "rounds_used": N, "final_score": ..., "improved": bool}
    """
    current_response = assistant_response
    improved = False

    for round_num in range(1, max_rounds + 1):
        yield {"type": "reflection_start", "round": round_num}

        evaluation = await evaluate_response(user_message, current_response, model)
        score = evaluation.get("score", 70)
        passed = evaluation.get("pass", True)
        issues = evaluation.get("issues", [])
        suggestion = evaluation.get("suggestion", "")

        yield {
            "type": "reflection_eval",
            "round": round_num,
            "score": score,
            "pass": passed,
            "issues": issues,
        }

        if passed and score >= min_score:
            yield {
                "type": "reflection_done",
                "rounds_used": round_num,
                "final_score": score,
                "improved": improved,
            }
            return

        # Correction needed
        corrected_parts = []
        async for token in correct_response(
            user_message, current_response, issues, suggestion, model
        ):
            corrected_parts.append(token)
            yield {"type": "reflection_correction_token", "content": token}

        current_response = "".join(corrected_parts)
        improved = True

    # Exhausted rounds
    yield {
        "type": "reflection_done",
        "rounds_used": max_rounds,
        "final_score": score,
        "improved": improved,
    }


def should_reflect(mode: str, message: str, response_length: int) -> bool:
    """Decide whether to trigger reflection based on context."""
    from app.config import settings
    if not settings.enable_reflection:
        return False
    # Only reflect in pro/ultra modes
    if mode not in ("pro", "ultra"):
        return False
    # Skip very short responses (greetings, confirmations)
    if response_length < 200:
        return False
    # Skip if message is trivial
    trivial_patterns = ["你好", "hi", "hello", "谢谢", "thanks", "ok"]
    if message.strip().lower() in trivial_patterns:
        return False
    return True
