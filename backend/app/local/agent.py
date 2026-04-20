from app.local.tools import LOCAL_TOOLS
from app.agents.tools import ALL_TOOLS, set_thread_context
from app.local.tools import set_local_thread_context
from app.models.provider import llm_provider
from app.memory.store import memory_store
from app.agents.context import should_summarize, summarize_messages, get_messages_for_context
from app.models.schemas import Message
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
import os
import json
from typing import AsyncGenerator


LOCAL_MODE_SYSTEM = """You are a Super Agent in LOCAL MODE. You ARE connected to the user's computer RIGHT NOW through a local client. You CAN see and operate their computer.

When the user asks about your abilities, DO NOT explain what you can't do. Instead, DEMONSTRATE by using tools. For example:
- If user asks "can you see my computer?", call local_get_system_info immediately and show the results.
- If user asks "what's on my computer?", call local_list_files on their home directory.

AVAILABLE TOOLS (use them actively, don't just describe them):
- local_get_system_info: Get OS, hostname, disk, memory info. USE THIS FIRST to prove you're connected.
- local_execute_bash: Run any terminal command on the user's computer
- local_read_file: Read any file on the user's computer
- local_write_file: Create or modify files on the user's computer
- local_list_files: Browse the user's file system
- local_execute_python: Run Python code with the user's local environment
- local_open_app: Open applications (Chrome, VS Code, Finder, Terminal, etc.)
- web_search: Search the internet
- web_fetch: Read web pages
- calculate: Math calculations
- get_current_time: Current date/time

RULES:
1. ALWAYS use tools to answer questions about the user's computer. Never say "I cannot" - you CAN.
2. When first connected, call local_get_system_info to learn about the computer.
3. Be careful with destructive commands (rm, format, etc.) - warn the user first.
4. Use the user's actual file paths (check with local_list_files or local_get_system_info first).
5. Respond in the same language as the user.
6. DO NOT give disclaimers about not being able to access the computer. You ARE connected.
"""


class LocalAgent:
    async def handle_message(
        self,
        message: str,
        thread_messages: list[Message],
        model: str | None = None,
        thread_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key or api_key == "sk-your-api-key-here":
            yield json.dumps({
                "type": "token",
                "content": "**Local Mode requires API Key.** Please configure it in Settings.",
            })
            yield json.dumps({"type": "done"})
            return

        from app.local.gateway import local_gateway
        client = local_gateway.get_client_for_thread(thread_id) if thread_id else None
        if not client:
            yield json.dumps({
                "type": "token",
                "content": "**No local client connected.**\n\nTo use Local Mode, run the local client on your computer:\n```bash\npython local_client.py\n```\nThen connect it to this thread.",
            })
            yield json.dumps({"type": "done"})
            return

        if thread_id:
            set_thread_context(thread_id)
            set_local_thread_context(thread_id)

        memory_context = await memory_store.get_context_for_query(message)
        system_content = LOCAL_MODE_SYSTEM
        if memory_context:
            system_content += f"\n\nUser context:\n{memory_context}"

        combined_tools = ALL_TOOLS + LOCAL_TOOLS

        chat_model = llm_provider.get_chat_model(model, streaming=True)
        agent = create_react_agent(chat_model, combined_tools)

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

        local_clients = local_gateway.list_clients()
        if local_clients:
            lc_messages.append(SystemMessage(content=(
                f"LOCAL CLIENT CONNECTED: {local_clients[0].get('info', {}).get('hostname', 'unknown')} "
                f"({local_clients[0].get('info', {}).get('os', 'unknown')}). "
                f"You ARE connected to this computer. Use local_get_system_info or other local_* tools to demonstrate."
            )))

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
                    is_local = tool_name.startswith("local_")
                    yield json.dumps({
                        "type": "tool_call",
                        "data": {
                            "tool": tool_name,
                            "input": str(tool_input)[:200],
                            "status": "running",
                            "local": is_local,
                        },
                    })

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    output = event.get("data", {}).get("output", "")
                    is_local = tool_name.startswith("local_")
                    yield json.dumps({
                        "type": "tool_result",
                        "data": {
                            "tool": tool_name,
                            "output": str(output)[:500],
                            "status": "completed",
                            "local": is_local,
                        },
                    })

            if not full_content and tool_calls_made:
                full_content = f"(Used tools: {', '.join(tool_calls_made)})"

        except Exception as e:
            error_msg = str(e)
            if "tool_call" in error_msg.lower() or "function_call" in error_msg.lower():
                chat_model_no_tools = llm_provider.get_chat_model(model, streaming=True)
                try:
                    simple_msgs = [SystemMessage(content=LOCAL_MODE_SYSTEM), HumanMessage(content=message)]
                    async for chunk in chat_model_no_tools.astream(simple_msgs):
                        if hasattr(chunk, "content") and chunk.content:
                            full_content += chunk.content
                            yield json.dumps({"type": "token", "content": chunk.content})
                except Exception as e2:
                    yield json.dumps({"type": "error", "content": str(e2)})
            else:
                yield json.dumps({"type": "error", "content": error_msg})

        yield json.dumps({"type": "done"})


local_agent = LocalAgent()
