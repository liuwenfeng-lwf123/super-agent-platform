from __future__ import annotations
import logging

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


logger = logging.getLogger(__name__)
try:
    from langchain.agents import create_agent as create_react_agent
except ImportError:  # pragma: no cover - older langchain fallback
    from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.agents.tool_runtime import is_policy_allowed
from app.models.provider import llm_provider


SPECULATION_SYSTEM = """You are preparing a speculative continuation for an AI coding assistant conversation.

Draft a likely next assistant response for the suggested follow-up prompt.
Treat this as tentative: do not claim to have run tools, changed files, or verified facts unless the provided context already says so.
Keep the draft concise, useful, and action-oriented.
"""


@dataclass
class SpeculationRecord:
    thread_id: str
    suggestion: str
    assistant_content: str
    user_message: str
    tool_summary: str | None
    model: str | None
    preview: dict[str, Any] | None
    created_at: str
    shadow_thread_id: str = ""
    status: str = "pending"
    draft: str = ""
    error: str = ""
    source: str = "speculative_agent"
    execution_mode: str = "tool_agent"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    executed_tool_summary: str = ""
    changes: list[dict[str, Any]] = field(default_factory=list)
    accepted_at: str = ""
    consumed_at: str = ""
    task: asyncio.Task | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "shadow_thread_id": self.shadow_thread_id,
            "suggestion": self.suggestion,
            "preview": self.preview,
            "created_at": self.created_at,
            "status": self.status,
            "draft": self.draft,
            "error": self.error,
            "source": self.source,
            "execution_mode": self.execution_mode,
            "tool_summary": self.executed_tool_summary or self.tool_summary or "",
            "tool_calls": self.tool_calls[-10:],
            "changes": self.changes,
            "accepted_at": self.accepted_at,
            "consumed_at": self.consumed_at,
        }


class SpeculationStore:
    def __init__(self):
        self._records: dict[str, SpeculationRecord] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._background_tasks: set[asyncio.Task] = set()

    def subscribe(self, thread_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(thread_id, []).append(queue)
        return queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue):
        queues = self._subscribers.get(thread_id, [])
        if queue in queues:
            queues.remove(queue)
        if not queues and thread_id in self._subscribers:
            del self._subscribers[thread_id]

    async def _publish(self, thread_id: str, payload: dict[str, Any]):
        for queue in list(self._subscribers.get(thread_id, [])):
            try:
                queue.put_nowait(payload)
            except Exception as e:
                logger.debug("Suppressed error in prompt_features: %s", e)
                continue

    async def _publish_record(self, record: SpeculationRecord):
        await self._publish(record.thread_id, {"type": "speculation_state", "data": record.to_public()})

    def _schedule_publish_record(self, record: SpeculationRecord):
        try:
            task = asyncio.get_running_loop().create_task(self._publish_record(record))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except RuntimeError:
            pass

    def _fallback_draft(self, suggestion: str, preview: dict[str, Any] | None) -> str:
        actions = (preview or {}).get("preview", []) if isinstance(preview, dict) else []
        if actions:
            return f"建议下一步：{suggestion}\n\n可以优先这样推进：\n" + "\n".join(
                f"- {action}" for action in actions[:3]
            )
        return f"建议下一步：{suggestion}"

    def _build_messages(self, record: SpeculationRecord) -> list[Any]:
        actions = []
        if record.preview:
            actions = record.preview.get("preview", [])[:3]
        body = [
            f"Previous user prompt:\n{record.user_message}",
            f"Likely next user prompt:\n{record.suggestion}",
            f"Previous assistant response:\n{record.assistant_content[:5000]}",
        ]
        if record.tool_summary:
            body.append(f"Recent tool summary:\n{record.tool_summary}")
        if actions:
            body.append("Suggested next actions:\n" + "\n".join(f"- {action}" for action in actions))
        return [
            SystemMessage(content=SPECULATION_SYSTEM),
            HumanMessage(content="\n\n".join(body)),
        ]

    def _build_agent_messages(self, record: SpeculationRecord) -> list[Any]:
        actions = []
        if record.preview:
            actions = record.preview.get("preview", [])[:3]
        lines = [
            f"Previous user prompt:\n{record.user_message}",
            f"Previous assistant response:\n{record.assistant_content[:5000]}",
            f"Likely next user prompt:\n{record.suggestion}",
            "Use read-only tools when helpful. If a file change would help, you may use write_file inside the isolated shadow workspace, then draft the tentative assistant reply.",
        ]
        if record.tool_summary:
            lines.append(f"Recent tool summary:\n{record.tool_summary}")
        if actions:
            lines.append("Suggested next actions:\n" + "\n".join(f"- {action}" for action in actions))
        return [
            SystemMessage(
                content=SPECULATION_SYSTEM
                + "\n\nYou are in speculative shadow-workspace mode. You may inspect files, search, fetch, analyze, and write files only inside the isolated shadow workspace. Do not use system-destructive tools or claim shadow changes have been applied to the real workspace."
            ),
            HumanMessage(content="\n\n".join(lines)),
        ]

    async def _run_agent_generation(self, record: SpeculationRecord) -> str:
        from app.agents.tool_runtime import (
            clear_runtime_context,
            get_runtime_context,
            get_tool_metadata,
            set_runtime_context,
            summarize_tool_names,
        )
        from app.agents.tools import get_all_tools, set_thread_context
        from app.runtime_backends import runtime_manager

        tools = [
            tool_obj
            for tool_obj in get_all_tools(include_deferred=True, enable_tool_search=True, wrap=True)
            if get_tool_metadata(tool_obj.name).is_read_only or tool_obj.name == "write_file"
        ]
        if not tools:
            raise RuntimeError("No speculation-safe tools available for speculation")

        if not record.shadow_thread_id:
            raise RuntimeError("Missing shadow thread for speculation")

        set_thread_context(record.shadow_thread_id)
        runtime_token = set_runtime_context(thread_id=record.shadow_thread_id, agent_id="speculation", mode="speculation")
        try:
            chat_model = llm_provider.get_chat_model(record.model, streaming=False)
            agent = create_react_agent(chat_model, tools)
            response = await agent.ainvoke(
                {"messages": self._build_agent_messages(record)},
                config={"recursion_limit": 12},
            )
            result_content = ""
            msgs = response.get("messages", [])
            for msg in reversed(msgs):
                if hasattr(msg, "content") and isinstance(msg.content, str) and msg.content:
                    result_content = msg.content
                    break
            ctx = get_runtime_context()
            record.tool_calls = [
                {
                    "tool": call.get("tool", ""),
                    "input": call.get("input", ""),
                    "category": call.get("category", ""),
                }
                for call in ctx.tool_calls[-10:]
            ]
            if record.tool_calls:
                record.executed_tool_summary = summarize_tool_names(
                    [call.get("tool", "") for call in record.tool_calls if call.get("tool")]
                )
            diff = runtime_manager.list_shadow_changes(record.shadow_thread_id)
            if not diff.get("error"):
                record.changes = diff.get("changes", [])
            return result_content.strip()
        finally:
            clear_runtime_context(runtime_token)

    async def _run_generation(self, record: SpeculationRecord):
        record.status = "running"
        await self._publish_record(record)
        try:
            record.draft = await self._run_agent_generation(record)
            if not record.draft:
                record.draft = self._fallback_draft(record.suggestion, record.preview)
            record.status = "completed"
            await self._publish_record(record)
        except asyncio.CancelledError:
            record.status = "cancelled"
            await self._publish_record(record)
            raise
        except Exception as exc:
            record.error = str(exc)
            try:
                record.source = "model_fallback"
                record.execution_mode = "model"
                model = llm_provider.get_chat_model(record.model, streaming=False)
                response = await model.ainvoke(self._build_messages(record))
                content = response.content if hasattr(response, "content") else str(response)
                record.draft = str(content).strip() or self._fallback_draft(record.suggestion, record.preview)
                record.status = "completed"
                await self._publish_record(record)
            except Exception as inner_exc:
                record.draft = self._fallback_draft(record.suggestion, record.preview)
                record.status = "completed"
                record.source = "fallback"
                record.execution_mode = "fallback"
                record.error = "; ".join(part for part in [record.error, str(inner_exc)] if part)
                await self._publish_record(record)

    def start(
        self,
        *,
        thread_id: str,
        suggestion: str,
        assistant_content: str,
        user_message: str,
        tool_summary: str | None,
        model: str | None,
        preview: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not thread_id or not suggestion:
            return None
        existing = self._records.get(thread_id)
        if existing is not None:
            self.clear(thread_id)
        record = SpeculationRecord(
            thread_id=thread_id,
            suggestion=suggestion,
            assistant_content=assistant_content,
            user_message=user_message,
            tool_summary=tool_summary,
            model=model,
            preview=preview,
            created_at=datetime.now().isoformat(),
            shadow_thread_id=f"spec-{thread_id}",
        )
        try:
            from app.runtime_backends import runtime_manager

            runtime_manager.create_shadow_workspace(thread_id, record.shadow_thread_id)
        except Exception as exc:
            record.error = str(exc)
            record.source = "shadow_setup_failed"
        record.task = asyncio.create_task(self._run_generation(record))
        self._records[thread_id] = record
        self._schedule_publish_record(record)
        return record.to_public()

    def get(self, thread_id: str) -> dict[str, Any] | None:
        record = self._records.get(thread_id)
        if record is None:
            return None
        return record.to_public()

    def clear(self, thread_id: str) -> bool:
        record = self._records.pop(thread_id, None)
        if record is None:
            return False
        if record.task and not record.task.done():
            record.task.cancel()
        if record.shadow_thread_id:
            try:
                from app.runtime_backends import runtime_manager

                runtime_manager.discard_shadow_workspace(record.shadow_thread_id)
            except Exception as e:
                logger.debug("Suppressed error in prompt_features: %s", e)
        record.status = "cleared"
        self._schedule_publish_record(record)
        return True

    def consume_if_matching(self, thread_id: str, user_message: str) -> dict[str, Any] | None:
        record = self._records.get(thread_id)
        if record is None:
            return None
        if not suggestion_matches(user_message, record.suggestion):
            return None
        if record.status != "completed":
            if record.task and not record.task.done():
                record.task.cancel()
            return None
        record.status = "consumed"
        record.consumed_at = datetime.now().isoformat()
        self._schedule_publish_record(record)
        return record.to_public()

    def accept(
        self,
        thread_id: str,
        paths: list[str] | None = None,
        hunks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        record = self._records.get(thread_id)
        if record is None:
            return None
        if record.status not in {"completed", "consumed", "partially_accepted"}:
            return {"error": "Speculation is not ready", "status": record.status}
        if not record.shadow_thread_id:
            return {"error": "Speculation shadow workspace missing"}
        from app.runtime_backends import runtime_manager

        result = runtime_manager.accept_shadow_workspace(record.shadow_thread_id, paths=paths, hunks=hunks)
        if result.get("status") == "accepted":
            record.accepted_at = datetime.now().isoformat()
            record.status = "accepted" if result.get("accepted_all") else "partially_accepted"
            record.changes = result.get("remaining_changes", [])
            self._schedule_publish_record(record)
        return {**record.to_public(), "accept_result": result}

    def get_shadow_changes(self, thread_id: str) -> dict[str, Any] | None:
        record = self._records.get(thread_id)
        if record is None:
            return None
        if not record.shadow_thread_id:
            return {"error": "Speculation shadow workspace missing"}

        from app.runtime_backends import runtime_manager

        return runtime_manager.list_shadow_changes(record.shadow_thread_id)

    def get_shadow_diff(self, thread_id: str) -> dict[str, Any] | None:
        record = self._records.get(thread_id)
        if record is None:
            return None
        if not record.shadow_thread_id:
            return {"error": "Speculation shadow workspace missing"}

        from app.runtime_backends import runtime_manager

        return runtime_manager.get_shadow_diff(record.shadow_thread_id)


speculation_store = SpeculationStore()


def _contains_any(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\u4e00-\u9fff]+", " ", text.lower())).strip()


def suggestion_matches(user_message: str, suggestion: str) -> bool:
    normalized_user = _normalize_text(user_message)
    normalized_suggestion = _normalize_text(suggestion)
    if not normalized_user or not normalized_suggestion:
        return False
    return (
        normalized_user == normalized_suggestion
        or normalized_user in normalized_suggestion
        or normalized_suggestion in normalized_user
    )


def should_suggest_prompt(
    thread_messages: list[Any],
    assistant_content: str,
    mode: str = "standard",
) -> bool:
    if not settings.enable_prompt_suggestions:
        return False
    if not is_policy_allowed("feature.prompt_suggestion", True):
        return False
    if mode in {"flash", "multi-agent", "ultra"}:
        return False
    if len(thread_messages) < 1:
        return False
    if not assistant_content.strip():
        return False
    if _contains_any(assistant_content, ["error", "failed", "exception", "traceback"]):
        return False
    return True


def build_prompt_suggestion(
    user_message: str,
    assistant_content: str,
    tool_summary: str | None = None,
) -> str | None:
    if _contains_any(assistant_content, ["测试", "test", "验证", "verify"]):
        return "继续运行验证并修复剩余问题"
    if _contains_any(assistant_content, ["计划", "步骤", "plan", "step"]):
        return "按刚才的计划开始执行第一步"
    if _contains_any(assistant_content, ["文件", ".py", ".ts", ".md", "function", "class"]):
        return "把关键改动落到代码里并做一次回归检查"
    if tool_summary and _contains_any(tool_summary, ["Researched", "research", "sources", "外部"]):
        return "基于这些资料整理成最终结论和行动建议"
    if _contains_any(user_message, ["优化", "改造", "refactor", "implement"]):
        return "继续实现下一批高优先级改动"
    return "继续推进下一步并给出验证结果"


def build_speculation_preview(
    suggestion: str | None,
    assistant_content: str,
    tool_summary: str | None = None,
) -> dict[str, Any] | None:
    if not suggestion:
        return None
    if not settings.enable_speculation:
        return None
    if not is_policy_allowed("feature.speculation", True):
        return None
    actions: list[str] = []
    if tool_summary:
        actions.append(f"复用当前上下文继续：{tool_summary}")
    if _contains_any(assistant_content, ["测试", "test", "验证", "verify"]):
        actions.append("优先跑验证或回归命令")
    if _contains_any(assistant_content, ["文件", ".py", ".ts", ".md"]):
        actions.append("优先定位相关文件并检查是否需要修改")
    if not actions:
        actions.append("基于建议继续展开实现或分析")
    return {
        "suggestion": suggestion,
        "preview": actions[:3],
    }


def start_speculation(
    thread_id: str,
    suggestion: str | None,
    assistant_content: str,
    user_message: str,
    tool_summary: str | None = None,
    model: str | None = None,
    preview: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not suggestion:
        return None
    if not settings.enable_speculation:
        return None
    if not is_policy_allowed("feature.speculation", True):
        return None
    return speculation_store.start(
        thread_id=thread_id,
        suggestion=suggestion,
        assistant_content=assistant_content,
        user_message=user_message,
        tool_summary=tool_summary,
        model=model,
        preview=preview,
    )


def consume_matching_speculation(thread_id: str, user_message: str) -> dict[str, Any] | None:
    if not thread_id:
        return None
    return speculation_store.consume_if_matching(thread_id, user_message)


def get_speculation(thread_id: str) -> dict[str, Any] | None:
    if not thread_id:
        return None
    return speculation_store.get(thread_id)


def get_speculation_changes(thread_id: str) -> dict[str, Any] | None:
    if not thread_id:
        return None
    return speculation_store.get_shadow_changes(thread_id)


def get_speculation_diff(thread_id: str) -> dict[str, Any] | None:
    if not thread_id:
        return None
    return speculation_store.get_shadow_diff(thread_id)


def accept_speculation(
    thread_id: str,
    paths: list[str] | None = None,
    hunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not thread_id:
        return None
    return speculation_store.accept(thread_id, paths=paths, hunks=hunks)


def clear_speculation(thread_id: str) -> bool:
    if not thread_id:
        return False
    return speculation_store.clear(thread_id)
