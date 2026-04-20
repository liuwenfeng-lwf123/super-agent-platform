from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from app.models.provider import llm_provider
from app.agents.tools import ALL_TOOLS, set_thread_context
from app.memory.store import memory_store
from app.agents.context import should_summarize, summarize_messages, get_messages_for_context
from app.models.schemas import Message
import os
import json
from typing import AsyncGenerator


SUPER_AGENT_SYSTEM = """You are a Super Agent, an intelligent AI assistant with powerful tools and a workspace.

You have access to the following tools:
- **web_search**: Search the internet for real-time information
- **web_fetch**: Fetch and read the full text content of a web page URL
- **execute_python**: Run Python code (can install packages, process data, create files)
- **execute_javascript**: Run JavaScript code
- **execute_bash**: Run bash shell commands (install packages, build tools, git, etc.)
- **write_file**: Write content to a file in the workspace
- **read_file**: Read a file from the workspace
- **list_files**: List files in the workspace directory
- **get_current_time**: Get current date and time
- **calculate**: Evaluate math expressions

You have a persistent workspace where you can create, read, and manage files.

Guidelines:
1. For research tasks: search the web, read results, then synthesize a comprehensive answer
2. For coding tasks: write code to files, install dependencies via bash, run and iterate
3. For report/writing tasks: research first, then write the full document to a file
4. For web development: create HTML/CSS/JS files, preview them
5. Use execute_bash to install packages (pip install, npm install) when needed
6. Always save important outputs to files using write_file so users can download them
7. Respond in the same language as the user
8. When creating web pages, save them as .html files and mention the filename"""

FLASH_SYSTEM = """You are a helpful AI assistant. Give concise, direct answers. No tools needed unless explicitly asked. Keep responses brief and to the point. Respond in the same language as the user."""

PRO_SYSTEM = SUPER_AGENT_SYSTEM + """

You are in PRO mode - you plan before executing. For complex tasks:
1. First, outline your approach and the steps you will take
2. Then execute each step methodically
3. Save all outputs to files
4. Provide a final summary of what was accomplished

Always think step by step and show your planning."""


class SuperAgent:
    async def handle_message(
        self,
        message: str,
        thread_messages: list[Message],
        model: str | None = None,
        skills: list[str] | None = None,
        mode: str = "standard",
        thread_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key or api_key == "sk-your-api-key-here":
            yield json.dumps({
                "type": "token",
                "content": "**Super Agent Platform is running!**\n\nPlease configure your API Key to start chatting.",
            })
            yield json.dumps({"type": "done"})
            return

        if thread_id:
            set_thread_context(thread_id)

        if mode == "flash":
            async for event in self._flash_flow(message, model):
                yield event
        elif mode == "pro":
            async for event in self._pro_flow(message, thread_messages, model):
                yield event
        elif mode == "ultra":
            async for event in self._ultra_flow(message, thread_messages, model):
                yield event
        elif mode == "multi-agent":
            async for event in self._ultra_flow(message, thread_messages, model):
                yield event
        else:
            async for event in self._standard_flow(message, thread_messages, model):
                yield event

    async def _flash_flow(
        self, message: str, model: str | None
    ) -> AsyncGenerator[str, None]:
        chat_model = llm_provider.get_chat_model(model, streaming=True)
        lc_messages = [SystemMessage(content=FLASH_SYSTEM), HumanMessage(content=message)]
        try:
            async for chunk in chat_model.astream(lc_messages):
                if hasattr(chunk, "content") and chunk.content:
                    yield json.dumps({"type": "token", "content": chunk.content})
        except Exception as e:
            yield json.dumps({"type": "error", "content": str(e)})
        yield json.dumps({"type": "done"})

    async def _standard_flow(
        self, message: str, thread_messages: list[Message], model: str | None
    ) -> AsyncGenerator[str, None]:
        memory_context = await memory_store.get_context_for_query(message)

        system_content = SUPER_AGENT_SYSTEM
        if memory_context:
            system_content += f"\n\nUser context:\n{memory_context}"

        chat_model = llm_provider.get_chat_model(model, streaming=True)
        agent = create_react_agent(chat_model, ALL_TOOLS)

        raw_msgs = [{"role": m.role, "content": m.content} for m in thread_messages]
        context_summary = None
        if should_summarize(raw_msgs):
            context_summary = await summarize_messages(raw_msgs)
        context_msgs = get_messages_for_context(raw_msgs, context_summary)

        lc_messages = [SystemMessage(content=system_content)]
        for m in context_msgs:
            if m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))
            elif m["role"] == "system" and context_summary:
                lc_messages.append(SystemMessage(content=m["content"]))

        lc_messages.append(HumanMessage(content=message))

        try:
            full_content = ""
            tool_calls_made = []

            async for event in agent.astream_events({"messages": lc_messages}, version="v2"):
                kind = event.get("event", "")

                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        if isinstance(chunk.content, str):
                            full_content += chunk.content
                            yield json.dumps({"type": "token", "content": chunk.content})

                elif kind == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    tool_input = event.get("data", {}).get("input", {})
                    tool_calls_made.append(tool_name)
                    yield json.dumps({
                        "type": "tool_call",
                        "data": {"tool": tool_name, "input": str(tool_input)[:200], "status": "running"},
                    })

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    output = event.get("data", {}).get("output", "")
                    yield json.dumps({
                        "type": "tool_result",
                        "data": {"tool": tool_name, "output": str(output)[:500], "status": "completed"},
                    })

            if not full_content and tool_calls_made:
                full_content = f"(Used tools: {', '.join(tool_calls_made)})"

        except Exception as e:
            error_msg = str(e)
            if "tool_call" in error_msg.lower() or "function_call" in error_msg.lower():
                chat_model_no_tools = llm_provider.get_chat_model(model, streaming=True)
                try:
                    simple_msgs = [SystemMessage(content=SUPER_AGENT_SYSTEM), HumanMessage(content=message)]
                    async for chunk in chat_model_no_tools.astream(simple_msgs):
                        if hasattr(chunk, "content") and chunk.content:
                            full_content += chunk.content
                            yield json.dumps({"type": "token", "content": chunk.content})
                except Exception as e2:
                    yield json.dumps({"type": "error", "content": str(e2)})
            else:
                yield json.dumps({"type": "error", "content": error_msg})

        yield json.dumps({"type": "done"})

    async def _pro_flow(
        self, message: str, thread_messages: list[Message], model: str | None
    ) -> AsyncGenerator[str, None]:
        memory_context = await memory_store.get_context_for_query(message)

        system_content = PRO_SYSTEM
        if memory_context:
            system_content += f"\n\nUser context:\n{memory_context}"

        chat_model = llm_provider.get_chat_model(model, streaming=True)
        agent = create_react_agent(chat_model, ALL_TOOLS)

        raw_msgs = [{"role": m.role, "content": m.content} for m in thread_messages]
        context_summary = None
        if should_summarize(raw_msgs):
            context_summary = await summarize_messages(raw_msgs)
        context_msgs = get_messages_for_context(raw_msgs, context_summary)

        lc_messages = [SystemMessage(content=system_content)]
        for m in context_msgs:
            if m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))
            elif m["role"] == "system" and context_summary:
                lc_messages.append(SystemMessage(content=m["content"]))

        lc_messages.append(HumanMessage(content=message))

        try:
            full_content = ""
            tool_calls_made = []

            async for event in agent.astream_events({"messages": lc_messages}, version="v2"):
                kind = event.get("event", "")
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        if isinstance(chunk.content, str):
                            full_content += chunk.content
                            yield json.dumps({"type": "token", "content": chunk.content})
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    tool_input = event.get("data", {}).get("input", {})
                    tool_calls_made.append(tool_name)
                    yield json.dumps({
                        "type": "tool_call",
                        "data": {"tool": tool_name, "input": str(tool_input)[:200], "status": "running"},
                    })
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    output = event.get("data", {}).get("output", "")
                    yield json.dumps({
                        "type": "tool_result",
                        "data": {"tool": tool_name, "output": str(output)[:500], "status": "completed"},
                    })

        except Exception as e:
            yield json.dumps({"type": "error", "content": str(e)})

        yield json.dumps({"type": "done"})

    async def _ultra_flow(
        self, message: str, thread_messages: list[Message], model: str | None
    ) -> AsyncGenerator[str, None]:
        from app.agents.orchestrator import orchestrator
        import asyncio

        yield json.dumps({"type": "agent_status", "data": {"agent_id": "planner", "status": "planning"}})

        try:
            plan = await orchestrator.plan(message, model)
        except Exception as e:
            yield json.dumps({"type": "error", "content": f"Planning failed: {str(e)}"})
            yield json.dumps({"type": "done"})
            return

        if not plan.get("needs_planning", False) or not plan.get("steps"):
            async for event in self._pro_flow(message, thread_messages, model):
                yield event
            return

        steps = plan["steps"]
        yield json.dumps({
            "type": "plan",
            "data": {"total": len(steps), "steps": steps, "reasoning": plan.get("reasoning", "")},
        })

        from app.agents.orchestrator import SubAgent
        agents = [
            SubAgent(agent_id=f"sub-{i+1}", task=step["task"], skill=step.get("skill", "research"), model=model)
            for i, step in enumerate(steps)
        ]

        for a in agents:
            yield json.dumps({"type": "agent_status", "data": {"agent_id": a.agent_id, "status": "running", "task": a.task}})

        results = await asyncio.gather(*[a.execute() for a in agents], return_exceptions=True)

        for agent, result in zip(agents, results):
            status = "completed" if not isinstance(result, Exception) else "failed"
            yield json.dumps({"type": "agent_status", "data": {"agent_id": agent.agent_id, "status": status}})

        summary_parts = []
        for a in agents:
            summary_parts.append(f"**{a.agent_id}** ({a.skill}): {a.result[:500]}")
        summary = "\n\n".join(summary_parts)

        chat_model = llm_provider.get_chat_model(model, streaming=True)
        lc_messages = [
            SystemMessage(content=SUPER_AGENT_SYSTEM),
            HumanMessage(content=f"Task: {message}\n\nSub-agent results:\n{summary}\n\nSynthesize a comprehensive answer."),
        ]

        try:
            async for chunk in chat_model.astream(lc_messages):
                if hasattr(chunk, "content") and chunk.content:
                    yield json.dumps({"type": "token", "content": chunk.content})
        except Exception as e:
            yield json.dumps({"type": "error", "content": str(e)})

        yield json.dumps({"type": "done"})


super_agent = SuperAgent()
