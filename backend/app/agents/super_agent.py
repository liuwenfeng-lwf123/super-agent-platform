try:
    from langchain.agents import create_agent as create_react_agent
except ImportError:  # pragma: no cover - older langchain fallback
    from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from app.models.provider import llm_provider
from app.agents.tools import extract_validation_result, get_all_tools, set_thread_context
from app.local.editor_state import build_editor_context_prompt
from app.agents.tool_runtime import (
    clear_runtime_context,
    consume_hook_events,
    consume_file_diffs,
    consume_permission_events,
    consume_tool_use_summary,
    set_runtime_context,
)
from app.agents.prompt_features import (
    build_prompt_suggestion,
    build_speculation_preview,
    consume_matching_speculation,
    start_speculation,
    should_suggest_prompt,
)
from app.memory.store import memory_store
from app.agents.context import should_summarize, summarize_messages, get_messages_for_context, auto_compact
from app.agents.cost_tracker import cost_tracker, estimate_tokens
from app.agents.hooks import hooks_registry
from app.skills.search import web_search_tool
from app.models.schemas import Message
import os
import json
import logging
import asyncio
import re
from typing import AsyncGenerator

from app.agents.intent_detect import (
    extract_direct_search_query as _extract_direct_search_query,
    message_requests_screenshot as _message_requests_screenshot,
    extract_direct_browser_screenshot_url as _extract_direct_browser_screenshot_url,
    extract_direct_browser_open_url as _extract_direct_browser_open_url,
    is_simple_open_screenshot_request as _is_simple_open_screenshot_request,
    is_standalone_screenshot_request as _is_standalone_screenshot_request,
    screenshot_target_for_message as _screenshot_target_for_message,
)

logger = logging.getLogger(__name__)


def _format_search_results(query: str, results: list[dict]) -> str:
    if not results:
        return f"没有搜到：`{query}`"
    lines = [f"**搜索结果：`{query}`**\n"]
    for index, result in enumerate(results, 1):
        title = result.get("title") or "无标题"
        body = result.get("body") or ""
        href = result.get("href") or ""
        lines.append(f"### {index}. {title}")
        if body:
            lines.append(body)
        if href:
            lines.append(f"[打开链接]({href})")
        lines.append("")
    lines.append("你可以继续说：`打开第 1 条` 或 `总结第 1 条`。")
    return "\n".join(lines).strip()


SUPER_AGENT_SYSTEM = """You are a Super Agent — a self-evolving AI assistant with powerful tools, system control, and the ability to create new tools.

## Core Tools
- **web_search**: Search the internet for real-time information
- **web_fetch**: Fetch and read web page content
- **execute_python / execute_javascript / execute_bash**: Run code in a sandboxed workspace
- **write_file / read_file / list_files**: Manage workspace files
- **get_editor_state / get_editor_diagnostics**: Inspect the current thread's active file, cursor, selection, and recent editor-reported diagnostics
- **get_current_time**: Get current date and time
- **calculate**: Evaluate math expressions

## System Tools
- **screenshot**: Take a screenshot of the screen
- **clipboard_read / clipboard_write**: Read/write system clipboard
- **system_info**: Get OS, CPU, memory, disk information
- **open_app**: Open applications (e.g. Safari, Terminal)
- **open_url**: Open URLs in the browser
- **notify**: Send desktop notifications
- **git_command**: Run git commands in any directory
- **http_request**: Make HTTP requests (GET/POST/PUT/DELETE)
- **pdf_extract**: Extract text from PDF files
- **summarize_url**: Fetch and clean article text from URLs

## Self-Evolution (Meta-Tools) 🧬
- **create_tool**: Write Python code to create a NEW tool that persists across sessions
- **list_custom_tools**: See all tools you've created
- **remove_custom_tool**: Remove a custom tool
- **create_skill**: Create a new skill template (specialized persona + instructions)
- **list_custom_skills**: See all skills you've created
- **view_evolution_log**: View history of self-improvements

## Dynamic Tool Discovery
- **tool_search**: Search deferred tools by capability when a tool you need is not currently exposed
- **run_discovered_tool**: Invoke a discovered deferred tool with a JSON argument object

## Guidelines
1. For research: search the web, read results, synthesize comprehensive answers
2. For coding: write code to files, install dependencies via bash, run and iterate
3. For system tasks: use screenshot, clipboard, system_info, open_app, etc.
4. **Self-evolution**: When you encounter a task you lack a tool for, CREATE one using create_tool!
   Example: If asked to check stock prices repeatedly, create a 'get_stock_price' tool.
5. Always save important outputs to files using write_file
6. Respond in the same language as the user
7. When creating web pages, save them as .html files
8. **Knowledge persistence**: Proactively save useful facts to memory with `remember`
9. When you learn the user's preferences, tools, or environment — remember them"""

from app.agents.learning_loop import SKILL_WRITING_PRINCIPLES

MEMORY_RECALL_GUARDRAILS = """

## Memory Recall Safeguards
- Memory can be stale. Treat it as historical context, not current truth.
- If memory references a file, function, flag, or behavior, verify it against the current project state before relying on it.
- If the user asks to ignore memory, do not use remembered facts in your answer.
- Prefer what you can read from the current code and files over remembered snapshots.
"""

FLASH_SYSTEM = """You are a helpful AI assistant. Give concise, direct answers. No tools needed unless explicitly asked. Keep responses brief and to the point. Respond in the same language as the user."""


PRO_SYSTEM = SUPER_AGENT_SYSTEM + SKILL_WRITING_PRINCIPLES + """

You are in PRO mode - you plan before executing. For complex tasks:
1. First, outline your approach and the steps you will take
2. Then execute each step methodically
3. Save all outputs to files
4. Provide a final summary of what was accomplished

Always think step by step and show your planning."""


def _format_llm_error(error: Exception | str, model: str | None = None) -> str:
    message = str(error)
    lower = message.lower()
    if "invalid_api_key" in lower or "invalid api key" in lower or "401" in lower or "incorrect api key" in lower:
        config = llm_provider.resolve_model_config(model)
        return (
            "**API Key 无效或已被接口拒绝。**\n\n"
            f"当前模型：`{config.display_name}` / `{config.model}`\n\n"
            f"Provider：`{config.provider}`\n\n"
            f"接口地址：`{config.base_url}`\n\n"
            "请按下面步骤处理：\n\n"
            f"1. 打开「设置 → 模型配置 → API Key」，选择 `{config.provider}`。\n"
            "2. 如果有已保存的错误 Key，先点「删除错误 Key」。\n"
            "3. 粘贴正确的 API Key 后点「加密保存 API Key」。\n"
            f"4. 如果你是通过环境变量 `{config.api_key_env}` 配置的，请修改环境变量后重启后端。"
        )
    return message


def _build_human_message(text: str, images: list[str] | None = None) -> HumanMessage:
    """Build a HumanMessage, optionally with images for multimodal models."""
    if not images:
        return HumanMessage(content=text)
    content: list[dict] = [{"type": "text", "text": text}]
    for img in images:
        if img.startswith("data:"):
            content.append({"type": "image_url", "image_url": {"url": img}})
        else:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}})
    return HumanMessage(content=content)


def _resolve_skill(name: str) -> dict | None:
    """Resolve a skill by name, checking both custom (evolution) and built-in registries.
    Returns a normalized dict or None."""
    if not name:
        return None
    # Try custom skills first (created via evolution.create_skill)
    try:
        from app.agents.evolution import skill_registry as _custom
        sd = _custom.get_skill(name)
        if sd:
            return sd
    except Exception as e:
        logger.debug("Custom skill lookup failed for %s: %s", name, e)
    # Fall back to built-in skills
    try:
        from app.skills.base import skill_registry as _builtin
        sk = _builtin.get(name) if hasattr(_builtin, 'get') else None
        if not sk and hasattr(_builtin, '_skills'):
            sk = _builtin._skills.get(name)
        if sk:
            # Normalize to dict shape
            if hasattr(sk, 'model_dump'):
                return sk.model_dump()
            if isinstance(sk, dict):
                return sk
    except Exception as e:
        logger.debug("Built-in skill lookup failed for %s: %s", name, e)
    # Fall back to agentskills.io SKILL.md discovery
    try:
        from app.skills.agentskills_compat import discover_agentskills
        for s in discover_agentskills():
            if s.name == name:
                return s.to_skill_config()
    except Exception as e:
        logger.debug("AgentSkills discovery failed for %s: %s", name, e)
    return None


def _should_ignore_memory(message: str) -> bool:
    lowered = message.lower()
    triggers = [
        "ignore memory",
        "don't use memory",
        "do not use memory",
        "忽略记忆",
        "不要用记忆",
        "别用记忆",
    ]
    return any(trigger in lowered for trigger in triggers)


# Hermes + Claude Code style context files injected into the system prompt.
# Search order: CWD (project) then ~/.hermes/ (user). First hit wins per file.
_CONTEXT_FILES = [
    ("MEMORY.md", "## Persistent Memory\n"),
    ("USER.md", "## User Profile\n"),
    ("AGENTS.md", "## Project Agent Notes\n"),
    (".hermes.md", "## Project Context (.hermes.md)\n"),
]


def _load_context_files(max_bytes_per_file: int = 4000) -> str:
    """Read Hermes/Claude Code context files and join into one prompt fragment.

    Looks in the current working directory first, then ~/.hermes/. Silently
    skips missing or unreadable files. Caps each file to `max_bytes_per_file`
    to prevent prompt bloat.
    """
    out_parts: list[str] = []
    search_dirs = [os.getcwd(), os.path.expanduser("~/.hermes")]
    seen: set[str] = set()
    for filename, label in _CONTEXT_FILES:
        if filename in seen:
            continue
        for d in search_dirs:
            path = os.path.join(d, filename)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read(max_bytes_per_file + 1)
                if not content.strip():
                    continue
                if len(content) > max_bytes_per_file:
                    content = content[:max_bytes_per_file] + "\n...[truncated]"
                out_parts.append(f"\n\n{label}{content}")
                seen.add(filename)
                break
            except Exception as e:
                logger.debug("Context file %s unreadable: %s", filename, e)
                continue
    return "".join(out_parts)


def _fallback_model_candidates(model: str | None) -> list[str]:
    return llm_provider.get_fallback_model_names(model)


def _filter_tools(tools: list, allowed: list[str] | None = None, disabled: list[str] | None = None) -> list:
    allowed_set = {name for name in (allowed or []) if name}
    disabled_set = {name for name in (disabled or []) if name}
    filtered = []
    for tool_obj in tools:
        name = getattr(tool_obj, "name", "")
        if allowed_set and name not in allowed_set:
            continue
        if disabled_set and name in disabled_set:
            continue
        filtered.append(tool_obj)
    return filtered


def _is_tool_enabled(tool_name: str, allowed: list[str] | None = None, disabled: list[str] | None = None) -> bool:
    allowed_set = {name for name in (allowed or []) if name}
    disabled_set = {name for name in (disabled or []) if name}
    return (not allowed_set or tool_name in allowed_set) and tool_name not in disabled_set


def _is_tool_not_disabled(tool_name: str, disabled: list[str] | None = None) -> bool:
    return tool_name not in {name for name in (disabled or []) if name}


def _format_screenshot_chat_content(result: str) -> str:
    match = re.search(r"(/tmp/screenshot_[A-Za-z0-9_.-]+\.png)", result)
    if not match:
        return result
    filename = os.path.basename(match.group(1))
    url = f"/api/screenshots/{filename}"
    return f"截图完成：\n\n![截图预览]({url})\n\n[打开原图]({url})"


def _merge_skill_tools(selected_tools: list[str] | None, skills: list[str] | None) -> list[str] | None:
    merged = [name for name in (selected_tools or []) if name]
    seen = set(merged)
    for skill_name in skills or []:
        skill = _resolve_skill(skill_name)
        if not skill:
            continue
        for tool_name in (skill.get("tools") or []) + (skill.get("allowed_tools") or []):
            if tool_name and tool_name not in seen:
                merged.append(tool_name)
                seen.add(tool_name)
    if "browser_open" in seen and "screenshot" in seen and "browser_open_and_screenshot" not in seen:
        merged.append("browser_open_and_screenshot")
    return merged or selected_tools


def _sanitize_dangling_tool_calls(messages: list) -> list:
    """Fix dangling assistant tool_calls that lack a corresponding ToolMessage.

    Some providers return forced-stop assistant messages with tool_calls that never
    get ToolMessage responses.  Re-sending these to the model causes API errors.
    This strips tool_calls metadata from such assistant messages and injects a
    placeholder ToolMessage so the conversation stays valid.
    """
    from langchain_core.messages import ToolMessage
    sanitized: list = []
    for i, msg in enumerate(messages):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            # Check if next messages include ToolMessages for these tool_call ids
            expected_ids = {tc.get("id") or tc.get("tool_call_id", "") for tc in msg.tool_calls if isinstance(tc, dict)}
            found_ids: set[str] = set()
            for j in range(i + 1, min(i + len(expected_ids) + 2, len(messages))):
                if hasattr(messages[j], "tool_call_id"):
                    found_ids.add(messages[j].tool_call_id)
            missing_ids = expected_ids - found_ids
            if missing_ids:
                # Strip tool_calls and add placeholder ToolMessages
                clean_msg = AIMessage(content=msg.content or "(tool call interrupted)")
                sanitized.append(clean_msg)
                for tid in missing_ids:
                    sanitized.append(ToolMessage(
                        content="[Tool call was interrupted and did not produce a result]",
                        tool_call_id=tid,
                    ))
                continue
        sanitized.append(msg)
    return sanitized


def _can_retry_with_fallback(
    error: Exception | str,
    *,
    attempt_index: int,
    total_attempts: int,
    emitted_output: bool,
    used_tools: bool = False,
) -> bool:
    return (
        attempt_index < total_attempts - 1
        and not emitted_output
        and not used_tools
        and llm_provider.should_retry_with_fallback(error)
    )


class SuperAgent:
    async def _load_memory(self, message: str, thread_id: str | None, use_provider_plugin: bool = True) -> str:
        """Load memory context — shared by standard & pro flows."""
        if _should_ignore_memory(message):
            return ""
        memory_context = ""
        if use_provider_plugin:
            try:
                from app.agents.provider_plugins import get_active_memory_provider
                active_mp = get_active_memory_provider()
            except Exception as e:
                logger.debug("Memory provider plugin unavailable: %s", e)
                active_mp = None
            if active_mp is not None:
                try:
                    memory_context = await active_mp.get_context_for_query(message)
                except Exception as e:
                    logger.warning("Active memory provider failed, falling back: %s", e)
                    memory_context = await memory_store.get_context_for_query(message)
            else:
                memory_context = await memory_store.get_context_for_query(message)
        else:
            memory_context = await memory_store.get_context_for_query(message)
        try:
            from app.memory.layered_store import layered_memory
            layered_context = layered_memory.build_context(message, agent_type="super_agent", session_id=thread_id or "")
            if layered_context:
                memory_context = f"{memory_context}\n\n{layered_context}".strip()
        except Exception as e:
            logger.debug("Layered memory unavailable: %s", e)
        return memory_context

    def _build_system_prompt(
        self,
        base_prompt: str,
        memory_context: str,
        thread_id: str | None,
        message: str,
    ) -> str:
        """Build the full system prompt — shared by standard & pro flows."""
        soul_content = ""
        try:
            from app.agents.self_evolution import load_soul
            soul_content = (load_soul() or "").strip()
        except Exception as e:
            logger.debug("Soul loading skipped: %s", e)

        if soul_content:
            system_content = f"{soul_content}\n\n---\n\n{base_prompt}"
        else:
            system_content = base_prompt

        system_content += _load_context_files()

        if memory_context:
            system_content += f"\n\nUser context:\n{memory_context}"

        editor_context = build_editor_context_prompt(thread_id or "")
        if editor_context:
            system_content += f"\n\n{editor_context}"

        try:
            from app.rag.store import knowledge_base
            kb_context = knowledge_base.get_context(message)
            if kb_context:
                system_content += f"\n\nRelevant knowledge base documents:\n{kb_context}"
        except Exception as e:
            logger.debug("RAG context unavailable: %s", e)

        # Per-skill model/effort routing + system prompt injection
        try:
            for sname in (self._active_skills if hasattr(self, '_active_skills') and self._active_skills else []):
                sd = _resolve_skill(sname)
                if sd:
                    if sd.get("system_prompt"):
                        system_content += f"\n\n## Active Skill: {sd.get('display_name', sname)}\n{sd['system_prompt']}"
                    try:
                        from app.agents.self_evolution import check_skill_env_requirements
                        missing = [r for r in check_skill_env_requirements(sd) if not r["is_set"]]
                        if missing:
                            system_content += f"\n\n⚠️ Missing env vars for '{sname}': {', '.join(r['name'] for r in missing)}"
                    except Exception as e:
                        logger.debug("Skill env check failed for %s: %s", sname, e)
        except Exception as e:
            logger.debug("Skill prompt injection failed: %s", e)

        return system_content

    def _resolve_effective_model(self, model: str | None) -> str | None:
        """Check active skills for a preferred model — shared by standard & pro flows."""
        try:
            for sname in (self._active_skills if hasattr(self, '_active_skills') and self._active_skills else []):
                sd = _resolve_skill(sname)
                if sd and sd.get("model") and not model:
                    return sd["model"]
        except Exception as e:
            logger.debug("Skill model resolution failed: %s", e)
        return model

    async def _build_lc_messages(
        self,
        system_content: str,
        context_msgs: list[dict],
        context_summary: str | None,
        message: str,
        images: list[str] | None,
        thread_id: str | None,
        flow_name: str = "standard",
    ) -> list:
        """Assemble LangChain messages with context engine plugin fallback — shared by standard & pro flows."""
        lc_messages = None
        try:
            from app.agents.provider_plugins import get_active_context_engine
            active_engine = get_active_context_engine()
        except Exception as e:
            logger.debug("Context engine plugin unavailable: %s", e)
            active_engine = None
        if active_engine is not None:
            try:
                built = await active_engine.build_context(system_content, context_msgs, message)
                if isinstance(built, list) and built:
                    tmp = []
                    for m in built:
                        role = (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) or "user"
                        content = (m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or ""
                        if role == "system":
                            tmp.append(SystemMessage(content=content))
                        elif role == "assistant":
                            tmp.append(AIMessage(content=content))
                        else:
                            tmp.append(HumanMessage(content=content))
                    lc_messages = tmp
            except Exception as e:
                logger.warning("Active ContextEngine failed in %s flow, falling back: %s", flow_name, e)
                lc_messages = None

        if lc_messages is None:
            try:
                from app.agents.self_evolution import inject_cache_breakpoints
                cache_blocks = inject_cache_breakpoints(system_content)
                lc_messages = [SystemMessage(content=cache_blocks)]
            except Exception as e:
                logger.debug("Cache breakpoint injection skipped: %s", e)
                lc_messages = [SystemMessage(content=system_content)]
            for m in context_msgs:
                if m["role"] == "user":
                    lc_messages.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    lc_messages.append(AIMessage(content=m["content"]))
                elif m["role"] == "system" and context_summary:
                    lc_messages.append(SystemMessage(content=m["content"]))
            lc_messages.append(_build_human_message(message, images))

        return _sanitize_dangling_tool_calls(lc_messages)

    async def _run_agent_loop(
        self,
        lc_messages: list,
        system_content: str,
        message: str,
        effective_model: str | None,
        tools: list,
        disabled_tools: list[str] | None,
        thread_id: str | None,
        flow_name: str = "standard",
    ) -> AsyncGenerator[str, None]:
        """Core LLM + tool agent loop — shared by standard & pro flows."""
        full_content = ""
        tool_calls_made = []
        last_tool_summary = None
        _consecutive_tool_errors = 0
        attempt_models = _fallback_model_candidates(effective_model)
        for attempt_index, attempt_model in enumerate(attempt_models):
            chat_model = llm_provider.get_chat_model(attempt_model, streaming=True)
            cost_tracker.set_current_model(attempt_model)
            agent = create_react_agent(chat_model, tools)
            try:
                async for event in agent.astream_events({"messages": lc_messages}, version="v2"):
                    kind = event.get("event", "")
                    if kind == "on_chat_model_stream":
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content") and chunk.content:
                            if isinstance(chunk.content, str):
                                full_content += chunk.content
                                yield json.dumps({"type": "token", "content": chunk.content})
                    elif kind == "on_chat_model_end":
                        llm_output = event.get("data", {}).get("output")
                        if llm_output:
                            cost_tracker.add_tokens_from_api_response(llm_output)
                    elif kind == "on_tool_start":
                        tool_name = event.get("name", "unknown")
                        tool_input = event.get("data", {}).get("input", {})
                        tool_calls_made.append(tool_name)
                        cost_tracker.add_tool_call()
                        _consecutive_tool_errors = 0
                        yield json.dumps({
                            "type": "tool_call",
                            "data": {"tool": tool_name, "input": str(tool_input)[:200], "status": "running"},
                        })
                    elif kind == "on_tool_end":
                        tool_name = event.get("name", "unknown")
                        output = event.get("data", {}).get("output", "")
                        output_str = str(output)
                        tool_status = "completed"
                        if "error" in output_str.lower()[:200] or "traceback" in output_str.lower()[:200]:
                            _consecutive_tool_errors += 1
                            tool_status = "error"
                            logger.warning("%s flow tool %s returned error (consecutive=%d): %.200s",
                                           flow_name, tool_name, _consecutive_tool_errors, output_str)
                        else:
                            _consecutive_tool_errors = 0
                        yield json.dumps({
                            "type": "tool_result",
                            "data": {"tool": tool_name, "output": output_str[:500], "status": tool_status},
                        })
                        validation_result = extract_validation_result(output)
                        if validation_result:
                            yield json.dumps({
                                "type": "validation_result",
                                "data": {"tool": tool_name, **validation_result},
                            })
                        for he in consume_hook_events():
                            yield json.dumps({"type": he.get("type", "hook_event"), "data": he})
                        for permission_event in consume_permission_events():
                            yield json.dumps({"type": "permission_decision", "data": permission_event})
                        for file_diff in consume_file_diffs():
                            yield json.dumps({"type": "file_diff", "data": file_diff})
                        summary = consume_tool_use_summary()
                        if summary:
                            last_tool_summary = summary
                            yield json.dumps({"type": "tool_summary", "data": {"summary": summary}})

                if not full_content and tool_calls_made:
                    full_content = f"(Used tools: {', '.join(tool_calls_made)})"
                if cost_tracker.has_active_tracking() and cost_tracker.current_input_tokens() == 0:
                    cost_tracker.add_tokens(
                        input_tokens=estimate_tokens(system_content + message),
                        output_tokens=estimate_tokens(full_content) if full_content else 0,
                    )
                break
            except Exception as e:
                error_msg = str(e)
                if "tool_call" in error_msg.lower() or "function_call" in error_msg.lower():
                    chat_model_no_tools = llm_provider.get_chat_model(attempt_model, streaming=True)
                    cost_tracker.set_current_model(attempt_model)
                    try:
                        simple_msgs = [SystemMessage(content=system_content), HumanMessage(content=message)]
                        async for chunk in chat_model_no_tools.astream(simple_msgs):
                            if hasattr(chunk, "content") and chunk.content:
                                full_content += chunk.content
                                yield json.dumps({"type": "token", "content": chunk.content})
                        break
                    except Exception as e2:
                        if _can_retry_with_fallback(e2, attempt_index=attempt_index, total_attempts=len(attempt_models), emitted_output=bool(full_content), used_tools=bool(tool_calls_made)):
                            logger.warning("%s flow model %s failed in no-tools fallback, retrying: %s", flow_name, attempt_model, e2)
                            continue
                        yield json.dumps({"type": "error", "content": _format_llm_error(e2, attempt_model)})
                        break
                    finally:
                        await llm_provider.aclose_model(chat_model_no_tools)
                elif _can_retry_with_fallback(
                    e,
                    attempt_index=attempt_index,
                    total_attempts=len(attempt_models),
                    emitted_output=bool(full_content),
                    used_tools=bool(tool_calls_made),
                ):
                    logger.warning("%s flow model %s failed, retrying with fallback model: %s", flow_name, attempt_model, e)
                    continue
                else:
                    yield json.dumps({"type": "error", "content": _format_llm_error(e, attempt_model)})
                    break
            finally:
                await llm_provider.aclose_model(chat_model)

        # Yield a sentinel so callers can retrieve loop results
        yield json.dumps({"type": "_loop_result", "full_content": full_content, "tool_calls_made": tool_calls_made, "last_tool_summary": last_tool_summary or ""})

    async def handle_message(
        self,
        message: str,
        thread_messages: list[Message],
        model: str | None = None,
        skills: list[str] | None = None,
        tools: list[str] | None = None,
        disabled_tools: list[str] | None = None,
        mode: str = "standard",
        thread_id: str | None = None,
        images: list[str] | None = None,
        enable_speculation: bool | None = None,
    ) -> AsyncGenerator[str, None]:
        from app.config import settings as _settings
        active_skills = skills or []
        tools = _merge_skill_tools(tools, active_skills)

        direct_browser_screenshot_url = _extract_direct_browser_screenshot_url(message)
        if direct_browser_screenshot_url and _is_simple_open_screenshot_request(message) and _is_tool_not_disabled("browser_open_and_screenshot", disabled_tools) and _is_tool_not_disabled("screenshot", disabled_tools):
            runtime_token = set_runtime_context(thread_id=thread_id or "", mode=mode)
            try:
                from app.agents.system_tools import browser_open_and_screenshot
                from app.agents.tool_runtime import record_tool_event

                shot_input = {"url": direct_browser_screenshot_url, "region": "full", "timeout": 30}
                yield json.dumps({"type": "tool_call", "data": {"tool": "browser_open_and_screenshot", "input": json.dumps(shot_input, ensure_ascii=False), "status": "running"}})
                shot_result = await asyncio.to_thread(browser_open_and_screenshot.invoke, shot_input)
                record_tool_event(
                    tool="browser_open_and_screenshot",
                    category="system",
                    thread_id=thread_id or "",
                    mode=mode,
                    input_preview=json.dumps(shot_input, ensure_ascii=False),
                    output_preview=str(shot_result)[:1000],
                    success=True,
                    source="direct",
                )
                shot_content = _format_screenshot_chat_content(str(shot_result))
                yield json.dumps({"type": "tool_result", "data": {"tool": "browser_open_and_screenshot", "status": "completed", "output": str(shot_result)[:500]}})
                yield json.dumps({"type": "token", "content": shot_content})
                yield json.dumps({"type": "done"})
                return
            finally:
                clear_runtime_context(runtime_token)

        if _is_standalone_screenshot_request(message) and _is_tool_not_disabled("screenshot", disabled_tools):
            runtime_token = set_runtime_context(thread_id=thread_id or "", mode=mode)
            try:
                from app.agents.system_tools import screenshot
                from app.agents.tool_runtime import record_tool_event

                shot_target = _screenshot_target_for_message(message)
                shot_input = {"region": "full", "wait_for_browser": shot_target == "browser", "browser": "Safari", "timeout": 8, "target": shot_target}
                yield json.dumps({"type": "tool_call", "data": {"tool": "screenshot", "input": json.dumps(shot_input, ensure_ascii=False), "status": "running"}})
                shot_result = await asyncio.to_thread(screenshot.invoke, shot_input)
                record_tool_event(
                    tool="screenshot",
                    category="system",
                    thread_id=thread_id or "",
                    mode=mode,
                    input_preview=json.dumps(shot_input, ensure_ascii=False),
                    output_preview=str(shot_result)[:1000],
                    success=True,
                    source="direct-current-page",
                )
                shot_content = _format_screenshot_chat_content(str(shot_result))
                yield json.dumps({"type": "tool_result", "data": {"tool": "screenshot", "status": "completed", "output": str(shot_result)[:500]}})
                yield json.dumps({"type": "token", "content": shot_content})
                yield json.dumps({"type": "done"})
                return
            finally:
                clear_runtime_context(runtime_token)

        direct_browser_open_url = _extract_direct_browser_open_url(message)
        if direct_browser_open_url and _is_tool_not_disabled("browser_open", disabled_tools):
            runtime_token = set_runtime_context(thread_id=thread_id or "", mode=mode)
            try:
                from app.agents.system_tools import browser_open
                from app.agents.tool_runtime import record_tool_event

                open_input = {"url": direct_browser_open_url, "browser": "Safari"}
                yield json.dumps({"type": "tool_call", "data": {"tool": "browser_open", "input": json.dumps(open_input, ensure_ascii=False), "status": "running"}})
                open_result = await asyncio.to_thread(browser_open.invoke, open_input)
                record_tool_event(
                    tool="browser_open",
                    category="system",
                    thread_id=thread_id or "",
                    mode=mode,
                    input_preview=json.dumps(open_input, ensure_ascii=False),
                    output_preview=str(open_result)[:1000],
                    success=True,
                    source="direct-open",
                )
                yield json.dumps({"type": "tool_result", "data": {"tool": "browser_open", "status": "completed", "output": str(open_result)[:500]}})
                yield json.dumps({"type": "token", "content": f"已打开：{direct_browser_open_url}"})
                yield json.dumps({"type": "done"})
                return
            finally:
                clear_runtime_context(runtime_token)

        if not llm_provider.has_api_key(model):
            yield json.dumps({
                "type": "token",
                "content": "**天工流已启动！**\n\n请配置 API Key 开始对话。",
            })
            yield json.dumps({"type": "done"})
            return

        direct_search_query = _extract_direct_search_query(message)
        if direct_search_query:
            if not _is_tool_enabled("web_search", tools, disabled_tools):
                yield json.dumps({"type": "error", "content": "当前对话的工具设置不允许使用网页搜索。"})
                yield json.dumps({"type": "done"})
                return
            try:
                results = await web_search_tool.search(direct_search_query, max_results=6)
                content = _format_search_results(direct_search_query, results)
            except Exception as exc:
                content = f"搜索失败：{exc}"
            for index in range(0, len(content), 180):
                yield json.dumps({"type": "token", "content": content[index:index + 180]})
            yield json.dumps({"type": "done"})
            return

        if thread_id:
            set_thread_context(thread_id)

        self._active_skills = active_skills

        runtime_token = set_runtime_context(thread_id=thread_id or "", mode=mode)

        cost_tracker.start_tracking(
            model=llm_provider.normalize_model_name(model),
            thread_id=thread_id or "",
            mode=mode,
        )

        # --- Hooks: SessionStart ---
        try:
            await hooks_registry.fire("SessionStart", {
                "thread_id": thread_id or "", "mode": mode,
                "skills": self._active_skills, "model": model or "",
            })
        except Exception as e:
            logger.debug("SessionStart hook failed: %s", e)

        try:
            try:
                prompt_results = await hooks_registry.fire("UserPromptSubmit", {
                    "thread_id": thread_id or "",
                    "mode": mode,
                    "skills": self._active_skills,
                    "model": model or "",
                    "prompt": message,
                })
                for result in prompt_results:
                    if result.modified_input:
                        modified = result.modified_input.get("prompt") or result.modified_input.get("message")
                        if isinstance(modified, str) and modified:
                            message = modified
                    if result.decision == "deny":
                        reason = result.reason or "Denied by UserPromptSubmit hook"
                        yield json.dumps({"type": "error", "content": reason})
                        usage = cost_tracker.finish_tracking()
                        yield json.dumps({"type": "done", "usage": usage})
                        return
            except Exception as exc:
                logger.warning("UserPromptSubmit hook failed: %s", exc)

            if thread_id and mode in {"standard", "pro"}:
                speculative = consume_matching_speculation(thread_id, message)
                if speculative:
                    yield json.dumps({"type": "speculation_hit", "data": speculative})
                    yield json.dumps({"type": "token", "content": speculative.get("draft", "")})
                    if cost_tracker.has_active_tracking() and cost_tracker.current_input_tokens() == 0:
                        cost_tracker.add_tokens(
                            input_tokens=estimate_tokens(message),
                            output_tokens=estimate_tokens(speculative.get("draft", "")),
                        )
                    usage = cost_tracker.finish_tracking()
                    yield json.dumps({"type": "done", "usage": usage})
                    return

            if cost_tracker.is_over_budget():
                status = cost_tracker.get_budget_status()
                yield json.dumps({"type": "error", "content": f"Budget exceeded: ${status['spent']:.4f} / ${status['limit']:.4f}"})
                yield json.dumps({"type": "done"})
                return

            _speculation = enable_speculation if enable_speculation is not None else _settings.enable_speculation

            # Skill-dedicated workflow: if exactly one built-in skill is active,
            # try its execute_skill path (multi-step workflow with dedicated prompt).
            if active_skills and len(active_skills) == 1 and mode in ("standard", "pro"):
                from app.skills.base import skill_registry, execute_skill as _exec_skill
                _skill_obj = skill_registry.get(active_skills[0])
                if _skill_obj is not None:
                    yield json.dumps({"type": "skill_start", "data": {"skill": active_skills[0], "display_name": _skill_obj.display_name}})
                    async for event in _exec_skill(active_skills[0], message, model, thread_id or "_default"):
                        yield event
                    return

            if mode == "flash":
                async for event in self._flash_flow(message, model, images):
                    yield event
            elif mode == "pro":
                async for event in self._pro_flow(message, thread_messages, model, images, thread_id, _speculation, tools, disabled_tools):
                    yield event
            elif mode == "ultra":
                async for event in self._ultra_flow(message, thread_messages, model):
                    yield event
            elif mode == "multi-agent":
                async for event in self._ultra_flow(message, thread_messages, model):
                    yield event
            else:
                async for event in self._standard_flow(message, thread_messages, model, images, thread_id, _speculation, tools, disabled_tools):
                    yield event
        finally:
            clear_runtime_context(runtime_token)

    async def _flash_flow(
        self, message: str, model: str | None, images: list[str] | None = None
    ) -> AsyncGenerator[str, None]:
        lc_messages = [SystemMessage(content=FLASH_SYSTEM), _build_human_message(message, images)]
        full_out = ""
        attempt_models = _fallback_model_candidates(model)
        for attempt_index, attempt_model in enumerate(attempt_models):
            chat_model = llm_provider.get_chat_model(attempt_model, streaming=True)
            cost_tracker.set_current_model(attempt_model)
            attempt_output = ""
            try:
                last_chunk = None
                async for chunk in chat_model.astream(lc_messages):
                    if hasattr(chunk, "content") and chunk.content:
                        attempt_output += chunk.content
                        full_out += chunk.content
                        yield json.dumps({"type": "token", "content": chunk.content})
                    last_chunk = chunk
                if last_chunk:
                    cost_tracker.add_tokens_from_api_response(last_chunk)
                break
            except Exception as e:
                if _can_retry_with_fallback(
                    e,
                    attempt_index=attempt_index,
                    total_attempts=len(attempt_models),
                    emitted_output=bool(attempt_output),
                ):
                    logger.warning("Flash flow model %s failed, retrying with fallback: %s", attempt_model, e)
                    continue
                yield json.dumps({"type": "error", "content": str(e)})
                break
            finally:
                await llm_provider.aclose_model(chat_model)
        # Always ensure token estimation (fallback if API didn't return usage)
        if cost_tracker.has_active_tracking() and cost_tracker.current_input_tokens() == 0:
            cost_tracker.add_tokens(
                input_tokens=estimate_tokens(FLASH_SYSTEM + message),
                output_tokens=estimate_tokens(full_out) if full_out else 0,
            )

        # --- Evolution trace recording (flash flow) ---
        try:
            from app.agents.self_evolution import evolution_controller, TraceEntry
            from datetime import datetime as _dt
            _trace = TraceEntry(
                timestamp=_dt.now().isoformat(),
                thread_id="",
                skill_name="_default",
                user_input=message[:500],
                agent_output=full_out[:1000],
                tool_calls=[],
                success=bool(full_out and len(full_out) > 10),
            )
            evolution_controller.trace_collector.record(_trace)
        except Exception as e:
            logger.debug("Evolution trace recording (flash) failed: %s", e)

        usage = cost_tracker.finish_tracking()
        yield json.dumps({"type": "done", "usage": usage})

    async def _shared_flow(
        self,
        message: str,
        thread_messages: list[Message],
        model: str | None,
        flow_name: str,
        base_prompt: str,
        *,
        images: list[str] | None = None,
        thread_id: str | None = None,
        enable_speculation: bool = True,
        allowed_tools: list[str] | None = None,
        disabled_tools: list[str] | None = None,
        use_memory_provider: bool = True,
        use_frozen_memory: bool = True,
        enable_screenshot_fallback: bool = False,
        enable_session_search: bool = False,
    ) -> AsyncGenerator[str, None]:
        # --- Memory ---
        memory_context = await self._load_memory(message, thread_id, use_provider_plugin=use_memory_provider)
        if use_frozen_memory and thread_id and memory_context:
            from app.agents.learning_loop import frozen_memory
            memory_context = frozen_memory.capture(thread_id, memory_context)

        # --- System prompt ---
        system_content = self._build_system_prompt(base_prompt, memory_context, thread_id, message)
        effective_model = self._resolve_effective_model(model)

        # --- Tools ---
        include_skill_deferred_tools = bool(allowed_tools)
        tools = _filter_tools(get_all_tools(include_deferred=include_skill_deferred_tools, enable_tool_search=True), allowed_tools, disabled_tools)

        # --- Context compaction ---
        raw_msgs = [{"role": m.role, "content": m.content} for m in thread_messages]
        compact_summary, compact_level = await auto_compact(raw_msgs)
        if compact_summary and compact_level != "none":
            context_summary = compact_summary
            if thread_id:
                try:
                    from app.agents.store import thread_store
                    t = await thread_store.get(thread_id)
                    if t:
                        t.compact_summary = compact_summary
                        await thread_store.update_thread(t)
                except Exception as e:
                    logger.debug("Failed to persist compact summary for thread %s: %s", thread_id, e)
        elif should_summarize(raw_msgs):
            context_summary = await summarize_messages(raw_msgs)
        else:
            context_summary = None
        context_msgs = get_messages_for_context(raw_msgs, context_summary)

        # --- Assemble LangChain messages ---
        lc_messages = await self._build_lc_messages(
            system_content, context_msgs, context_summary, message, images, thread_id, flow_name,
        )

        # --- Agent loop (delegated to shared helper) ---
        full_content = ""
        tool_calls_made = []
        last_tool_summary = None
        async for event in self._run_agent_loop(
            lc_messages, system_content, message, effective_model, tools, disabled_tools, thread_id, flow_name,
        ):
            parsed = json.loads(event)
            if parsed.get("type") == "_loop_result":
                full_content = parsed["full_content"]
                tool_calls_made = parsed["tool_calls_made"]
                last_tool_summary = parsed["last_tool_summary"] or None
            else:
                yield event

        # --- Screenshot fallback (standard-only) ---
        if enable_screenshot_fallback and _message_requests_screenshot(message) and not any(name in {"screenshot", "browser_open_and_screenshot"} for name in tool_calls_made) and _is_tool_not_disabled("screenshot", disabled_tools):
            try:
                from app.agents.system_tools import browser_wait_for_ready, screenshot
                from app.agents.tool_runtime import record_tool_event

                wait_input = {"url": "", "browser": "Safari", "timeout": 12}
                yield json.dumps({"type": "tool_call", "data": {"tool": "browser_wait_for_ready", "input": json.dumps(wait_input, ensure_ascii=False), "status": "running"}})
                wait_result = await asyncio.to_thread(browser_wait_for_ready.invoke, wait_input)
                record_tool_event(
                    tool="browser_wait_for_ready",
                    category="system",
                    thread_id=thread_id or "",
                    mode=flow_name,
                    input_preview=json.dumps(wait_input, ensure_ascii=False),
                    output_preview=str(wait_result)[:1000],
                    success=True,
                    source="screenshot-fallback",
                )
                yield json.dumps({"type": "tool_result", "data": {"tool": "browser_wait_for_ready", "status": "completed", "output": str(wait_result)[:500]}})

                shot_input = {"region": "full", "wait_for_browser": True, "browser": "Safari", "timeout": 8}
                yield json.dumps({"type": "tool_call", "data": {"tool": "screenshot", "input": json.dumps(shot_input, ensure_ascii=False), "status": "running"}})
                shot_result = await asyncio.to_thread(screenshot.invoke, shot_input)
                record_tool_event(
                    tool="screenshot",
                    category="system",
                    thread_id=thread_id or "",
                    mode=flow_name,
                    input_preview=json.dumps(shot_input, ensure_ascii=False),
                    output_preview=str(shot_result)[:1000],
                    success=True,
                    source="screenshot-fallback",
                )
                shot_content = _format_screenshot_chat_content(str(shot_result))
                full_content = f"{full_content}\n\n{shot_content}".strip()
                tool_calls_made.append("screenshot")
                yield json.dumps({"type": "tool_result", "data": {"tool": "screenshot", "status": "completed", "output": str(shot_result)[:500]}})
                yield json.dumps({"type": "token", "content": f"\n\n{shot_content}"})
            except Exception as exc:
                yield json.dumps({"type": "error", "content": f"截图兜底失败：{exc}"})

        # --- Post-processing: session search, nudge, skill suggestion (standard-only) ---
        if enable_session_search:
            from app.agents.learning_loop import session_search_db, nudge_manager
            try:
                session_search_db.store(thread_id or "", "user", message)
                session_search_db.store(thread_id or "", "assistant", full_content, tool_calls=tool_calls_made)
            except Exception as e:
                logger.debug("Session search store failed: %s", e)

            try:
                nudge = nudge_manager.tick()
                if nudge:
                    yield json.dumps({"type": "nudge", "content": nudge})
            except Exception as e:
                logger.debug("Nudge tick failed: %s", e)

            try:
                from app.agents.learning_loop import should_suggest_skill_creation, get_skill_creation_hint
                if should_suggest_skill_creation(tool_calls_made):
                    yield json.dumps({"type": "skill_suggestion", "content": get_skill_creation_hint(tool_calls_made)})
            except Exception as e:
                logger.debug("Skill suggestion check failed: %s", e)

        # --- Reflection: self-verification & correction (pro/ultra only) ---
        try:
            from app.agents.reflection import should_reflect, reflect_and_correct
            if should_reflect(flow_name, message, len(full_content)):
                corrected_content = ""
                async for refl_event in reflect_and_correct(message, full_content, effective_model):
                    if refl_event["type"] == "reflection_correction_token":
                        corrected_content += refl_event["content"]
                    yield json.dumps(refl_event)
                if corrected_content:
                    full_content = corrected_content
        except Exception as e:
            logger.debug("Reflection step skipped: %s", e)

        # --- Post-processing: memory extraction (both flows) ---
        try:
            from app.memory.extract_memories import memory_extractor
            all_msgs = raw_msgs + [{"role": "user", "content": message}, {"role": "assistant", "content": full_content}]
            extracted = await memory_extractor.maybe_extract(all_msgs, session_id=thread_id or "")
            if extracted:
                yield json.dumps({"type": "memory_extracted", "count": len(extracted), "memories": [m["key"] for m in extracted]})
        except Exception as e:
            logger.debug("Memory extraction failed: %s", e)

        try:
            from app.agents.magic_docs import magic_docs
            magic_docs.record_session(thread_id or "", message, full_content, last_tool_summary)
        except Exception as e:
            logger.debug("Magic docs recording failed: %s", e)

        # --- Speculation ---
        if enable_speculation and should_suggest_prompt(thread_messages, full_content, mode=flow_name):
            suggestion = build_prompt_suggestion(message, full_content, last_tool_summary)
            preview = build_speculation_preview(suggestion, full_content, last_tool_summary)
            if suggestion:
                speculation = start_speculation(thread_id or "", suggestion, full_content, message, last_tool_summary, model, preview)
                yield json.dumps({
                    "type": "prompt_suggestion",
                    "data": {"suggestion": suggestion, "speculation": preview, "background": speculation},
                })

        # --- Hooks: Stop ---
        try:
            stop_data: dict = {
                "thread_id": thread_id or "", "tool_calls": tool_calls_made,
                "content_length": len(full_content),
            }
            if flow_name != "standard":
                stop_data["flow"] = flow_name
            await hooks_registry.fire("Stop", stop_data)
        except Exception as e:
            logger.debug("Stop hook failed: %s", e)

        # --- Evolution trace recording (auto-collects execution data) ---
        try:
            from app.agents.self_evolution import evolution_controller, TraceEntry
            from datetime import datetime as _dt
            _skill_name = (self._active_skills[0] if self._active_skills else "_default")
            _trace = TraceEntry(
                timestamp=_dt.now().isoformat(),
                thread_id=thread_id or "",
                skill_name=_skill_name,
                user_input=message[:500],
                agent_output=full_content[:1000],
                tool_calls=[{"name": t} for t in tool_calls_made],
                cost_usd=cost_tracker.current_cost_usd() if hasattr(cost_tracker, 'current_cost_usd') else 0.0,
                success=bool(full_content and len(full_content) > 10),
                score=None,
            )
            evolution_controller.trace_collector.record(_trace)
        except Exception as e:
            logger.debug("Evolution trace recording failed: %s", e)

        usage = cost_tracker.finish_tracking()
        yield json.dumps({"type": "done", "usage": usage})

    async def _standard_flow(
        self,
        message: str,
        thread_messages: list[Message],
        model: str | None,
        images: list[str] | None = None,
        thread_id: str | None = None,
        enable_speculation: bool = True,
        allowed_tools: list[str] | None = None,
        disabled_tools: list[str] | None = None,
    ) -> AsyncGenerator[str, None]:
        base_prompt = SUPER_AGENT_SYSTEM + SKILL_WRITING_PRINCIPLES + MEMORY_RECALL_GUARDRAILS
        async for event in self._shared_flow(
            message, thread_messages, model, "standard", base_prompt,
            images=images, thread_id=thread_id, enable_speculation=enable_speculation,
            allowed_tools=allowed_tools, disabled_tools=disabled_tools,
            use_memory_provider=True, use_frozen_memory=True,
            enable_screenshot_fallback=True, enable_session_search=True,
        ):
            yield event

    async def _pro_flow(
        self,
        message: str,
        thread_messages: list[Message],
        model: str | None,
        images: list[str] | None = None,
        thread_id: str | None = None,
        enable_speculation: bool = True,
        allowed_tools: list[str] | None = None,
        disabled_tools: list[str] | None = None,
    ) -> AsyncGenerator[str, None]:
        base_prompt = PRO_SYSTEM + MEMORY_RECALL_GUARDRAILS
        async for event in self._shared_flow(
            message, thread_messages, model, "pro", base_prompt,
            images=images, thread_id=thread_id, enable_speculation=enable_speculation,
            allowed_tools=allowed_tools, disabled_tools=disabled_tools,
            use_memory_provider=False, use_frozen_memory=False,
            enable_screenshot_fallback=False, enable_session_search=False,
        ):
            yield event

    async def _ultra_flow(
        self, message: str, thread_messages: list[Message], model: str | None
    ) -> AsyncGenerator[str, None]:
        from app.agents.orchestrator import orchestrator

        # Phase 1: Planning
        yield json.dumps({"type": "agent_status", "data": {"agent_id": "planner", "status": "planning", "task": "Analyzing task and creating execution plan"}})

        try:
            plan = await orchestrator.plan(message, model)
        except Exception as e:
            yield json.dumps({"type": "error", "content": f"Planning failed: {str(e)}"})
            yield json.dumps({"type": "done"})
            return

        # Simple tasks → fallback to Pro mode
        if not plan.get("needs_planning", False) or not plan.get("steps"):
            yield json.dumps({"type": "agent_status", "data": {"agent_id": "planner", "status": "completed", "task": "Task is simple, using single agent"}})
            async for event in self._pro_flow(message, thread_messages, model):
                yield event
            return

        steps = plan["steps"]
        reasoning = plan.get("reasoning", "")
        yield json.dumps({
            "type": "plan",
            "data": {"total": len(steps), "steps": steps, "reasoning": reasoning},
        })
        yield json.dumps({"type": "agent_status", "data": {"agent_id": "planner", "status": "completed", "task": f"Created {len(steps)} parallel sub-tasks"}})

        # Phase 2: Parallel execution with tool-equipped agents
        agent_results = []
        async for event in orchestrator.execute_parallel(steps, model, original_task=message):
            yield json.dumps(event)
            if event.get("type") == "agents_completed":
                agent_results = event["data"].get("results", [])

        # Phase 3: Synthesis — stream the final combined answer
        yield json.dumps({"type": "agent_status", "data": {"agent_id": "synthesizer", "status": "running", "task": "Combining all agent results"}})

        async for token in orchestrator.synthesize(message, agent_results, model):
            yield json.dumps({"type": "token", "content": token})

        yield json.dumps({"type": "agent_status", "data": {"agent_id": "synthesizer", "status": "completed"}})
        # Estimate tokens from actual content rather than hardcoded multipliers
        total_results_text = " ".join(str(r) for r in agent_results)
        cost_tracker.add_tokens(
            input_tokens=estimate_tokens(message),
            output_tokens=estimate_tokens(total_results_text) if total_results_text else 0,
        )
        for r in agent_results:
            cost_tracker.add_agent_spawn()
        usage = cost_tracker.finish_tracking()
        yield json.dumps({"type": "done", "usage": usage})


super_agent = SuperAgent()
