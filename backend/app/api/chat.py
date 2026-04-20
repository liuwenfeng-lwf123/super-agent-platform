from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import StreamingResponse, HTMLResponse
from app.models.schemas import ChatRequest, Message
from app.agents.super_agent import super_agent
from app.agents.store import thread_store
from app.sandbox.manager import sandbox_executor
import json
import os
import uuid

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat")
async def chat(request: ChatRequest):
    if request.thread_id:
        thread = await thread_store.get(request.thread_id)
        if not thread:
            thread = await thread_store.create()
    else:
        thread = await thread_store.create()

    user_msg = Message(role="user", content=request.message, thread_id=thread.id)
    await thread_store.add_message(thread.id, user_msg)

    async def event_generator():
        full_content = []

        async for event_str in super_agent.handle_message(
            message=request.message,
            thread_messages=thread.messages,
            model=request.model,
            skills=request.skills,
            mode=request.mode or "standard",
            thread_id=thread.id,
        ):
            event_data = json.loads(event_str)
            if event_data.get("type") == "token":
                full_content.append(event_data.get("content", ""))
                yield f"event: token\ndata: {event_str}\n\n"
            elif event_data.get("type") == "plan":
                yield f"event: plan\ndata: {event_str}\n\n"
            elif event_data.get("type") == "agent_status":
                yield f"event: agent_status\ndata: {event_str}\n\n"
            elif event_data.get("type") == "tool_call":
                yield f"event: tool_call\ndata: {event_str}\n\n"
            elif event_data.get("type") == "tool_result":
                yield f"event: tool_result\ndata: {event_str}\n\n"
            elif event_data.get("type") == "error":
                yield f"event: error\ndata: {event_str}\n\n"
            elif event_data.get("type") == "done":
                assistant_content = "".join(full_content)
                assistant_msg = Message(
                    role="assistant",
                    content=assistant_content,
                    thread_id=thread.id,
                )
                await thread_store.add_message(thread.id, assistant_msg)

                try:
                    from app.memory.extractor import extract_and_store_memory
                    await extract_and_store_memory(request.message, assistant_content)
                except Exception:
                    pass

                yield f"event: done\ndata: {json.dumps({'thread_id': thread.id, 'type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/threads")
async def list_threads():
    threads = await thread_store.list_threads()
    return [
        {
            "id": t.id,
            "title": t.title,
            "created_at": t.created_at.isoformat(),
            "updated_at": t.updated_at.isoformat(),
            "message_count": len(t.messages),
        }
        for t in threads
    ]


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    thread = await thread_store.get(thread_id)
    if not thread:
        return {"error": "Thread not found"}, 404
    return thread.model_dump(mode="json")


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    success = await thread_store.delete(thread_id)
    if success:
        return {"status": "deleted"}
    return {"error": "Thread not found"}, 404


@router.get("/models")
async def list_models():
    from app.models.provider import llm_provider
    return [m.model_dump() for m in llm_provider.list_models()]


@router.post("/models")
async def add_model(name: str, display_name: str, model: str, base_url: str = "", api_key_env: str = "OPENAI_API_KEY"):
    from app.models.provider import llm_provider
    from app.models.schemas import ModelConfig
    config = ModelConfig(name=name, display_name=display_name, model=model, base_url=base_url, api_key_env=api_key_env)
    llm_provider.add_model(config)
    return {"status": "added", "name": name}


@router.delete("/models/{name}")
async def remove_model(name: str):
    from app.models.provider import llm_provider
    success = llm_provider.remove_model(name)
    if success:
        return {"status": "removed"}
    return {"error": "Model not found"}


@router.get("/skills")
async def list_skills():
    from app.skills.base import skill_registry
    return [s.model_dump() for s in skill_registry.list_skills()]


@router.post("/skills/recommend")
async def recommend_skill(message: str):
    from app.skills.base import skill_registry
    msg = message.lower()
    recommendations = []

    if any(w in msg for w in ["research", "investigate", "analyze", "study", "survey", "explore"]):
        recommendations.append(skill_registry.get("deep-research"))
    if any(w in msg for w in ["web", "page", "website", "app", "html", "frontend", "dashboard"]):
        recommendations.append(skill_registry.get("web-page"))
    if any(w in msg for w in ["report", "document", "paper", "write up", "summary"]):
        recommendations.append(skill_registry.get("report-generation"))
    if any(w in msg for w in ["slide", "presentation", "ppt", "keynote"]):
        recommendations.append(skill_registry.get("slide-creation"))
    if any(w in msg for w in ["data", "csv", "excel", "chart", "visualization", "statistics"]):
        recommendations.append(skill_registry.get("data-analysis"))

    recommendations = [r for r in recommendations if r is not None]
    if not recommendations:
        recommendations = list(skill_registry._skills.values())[:3]

    return [{"name": r.name, "display_name": r.display_name, "description": r.description} for r in recommendations]


@router.get("/memory")
async def get_memory():
    from app.memory.store import memory_store

    entries = await memory_store.get_all()
    return [e.model_dump(mode="json") for e in entries]


@router.post("/memory")
async def add_memory(key: str, value: str, category: str = "knowledge"):
    from app.memory.store import memory_store

    entry = await memory_store.add(key, value, category)
    return entry.model_dump(mode="json")


@router.delete("/memory/{entry_id}")
async def delete_memory(entry_id: str):
    from app.memory.store import memory_store

    success = await memory_store.delete(entry_id)
    if success:
        return {"status": "deleted"}
    return {"error": "Entry not found"}, 404


@router.get("/health")
async def health():
    return {"status": "ok", "app": "天工流 TianGongFlow"}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    upload_dir = os.path.join("./data", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "file")[1]
    filepath = os.path.join(upload_dir, f"{file_id}{ext}")
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    return {
        "id": file_id,
        "filename": file.filename,
        "size": len(content),
        "path": filepath,
    }


@router.post("/sandbox/execute")
async def sandbox_execute(code: str, language: str = "python", timeout: int = 30):
    if language in ("python", "python3"):
        result = await sandbox_executor.execute_python(code, timeout)
    elif language in ("javascript", "js", "node"):
        result = await sandbox_executor.execute_javascript(code, timeout)
    else:
        return {"error": f"Unsupported language: {language}"}
    return result


@router.get("/workspace/{thread_id}/files")
async def list_workspace_files(thread_id: str, path: str = "."):
    result = await sandbox_executor.list_files(path, thread_id=thread_id)
    if not result["success"]:
        return {"error": result.get("error", "Failed to list files")}
    return result


@router.get("/workspace/{thread_id}/read")
async def read_workspace_file(thread_id: str, path: str):
    result = await sandbox_executor.read_file(path, thread_id=thread_id)
    if not result["success"]:
        return {"error": result.get("error", "Failed to read file")}
    return result


@router.get("/workspace/{thread_id}/download/{file_path:path}")
async def download_workspace_file(thread_id: str, file_path: str):
    from fastapi.responses import FileResponse
    work_dir = sandbox_executor.get_workspace_dir(thread_id)
    full_path = os.path.normpath(os.path.join(work_dir, file_path))
    if not full_path.startswith(os.path.normpath(work_dir)):
        return {"error": "Path traversal not allowed"}
    if not os.path.isfile(full_path):
        return {"error": "File not found"}
    return FileResponse(full_path, filename=os.path.basename(file_path))


@router.get("/outputs/{thread_id}/download/{file_path:path}")
async def download_output_file(thread_id: str, file_path: str):
    from fastapi.responses import FileResponse
    outputs_dir = sandbox_executor.get_outputs_dir(thread_id)
    full_path = os.path.normpath(os.path.join(outputs_dir, file_path))
    if not full_path.startswith(os.path.normpath(outputs_dir)):
        return {"error": "Path traversal not allowed"}
    if not os.path.isfile(full_path):
        return {"error": "File not found"}
    return FileResponse(full_path, filename=os.path.basename(file_path))


@router.post("/preview/html")
async def preview_html(html: str):
    return HTMLResponse(content=html)


# --- MCP Server Management ---
@router.get("/mcp/servers")
async def list_mcp_servers():
    from app.skills.mcp import mcp_registry
    return mcp_registry.list_servers()


@router.post("/mcp/servers")
async def add_mcp_server(name: str, url: str, api_key: str | None = None, enabled: bool = True):
    from app.skills.mcp import mcp_registry, MCPServerConfig
    config = MCPServerConfig(name=name, url=url, api_key=api_key, enabled=enabled)
    mcp_registry.register(config)
    await mcp_registry.discover_all()
    return {"status": "registered", "name": name}


@router.delete("/mcp/servers/{name}")
async def remove_mcp_server(name: str):
    from app.skills.mcp import mcp_registry
    mcp_registry.unregister(name)
    return {"status": "removed"}


@router.get("/mcp/tools")
async def list_mcp_tools():
    from app.skills.mcp import mcp_registry
    return mcp_registry.list_all_tools()


@router.post("/mcp/call")
async def call_mcp_tool(server_name: str, tool_name: str, arguments: dict = {}):
    from app.skills.mcp import mcp_registry
    result = await mcp_registry.call_tool(server_name, tool_name, arguments)
    return result


@router.post("/mcp/discover")
async def discover_mcp_tools():
    from app.skills.mcp import mcp_registry
    await mcp_registry.discover_all()
    return {"tools": mcp_registry.list_all_tools()}


# --- Observability / Tracing ---
@router.get("/tracing/status")
async def tracing_status():
    import os
    langsmith = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
    langfuse = os.getenv("LANGFUSE_TRACING", "false").lower() == "true"
    return {
        "langsmith": {"enabled": langsmith, "project": os.getenv("LANGSMITH_PROJECT", "")},
        "langfuse": {"enabled": langfuse, "public_key_set": bool(os.getenv("LANGFUSE_PUBLIC_KEY", ""))},
    }


@router.post("/tracing/configure")
async def configure_tracing(provider: str, enabled: bool = True):
    return {"status": "updated", "provider": provider, "enabled": enabled, "note": "Set env vars and restart backend to apply"}


# --- IM Channels ---
@router.get("/channels")
async def list_channels():
    from app.skills.channels import channel_manager
    return channel_manager.list_channels()


@router.get("/channels/{channel_type}/status")
async def channel_status(channel_type: str):
    from app.skills.channels import channel_manager
    return channel_manager.get_channel_status(channel_type)
