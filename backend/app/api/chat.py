from fastapi import APIRouter, Request, UploadFile, File, Header, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from app.models.schemas import ChatRequest, Message
from app.agents.super_agent import super_agent
from app.agents.store import thread_store
from app.runtime_backends import runtime_manager
from app.task_eval import (
    TaskEvalAgentConfig,
    TaskEvalRunner,
    evaluate_task_case_with_agent,
    list_task_eval_runs,
    load_task_cases,
    load_task_eval_run,
    render_task_eval_report,
    seed_dataset_overview,
)
import asyncio
import json
import os
import uuid
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# --- Sub-routers (split from monolithic chat.py) ---
from app.api.skills_routes import router as skills_router
from app.api.evolution_routes import router as evolution_router
from app.api.agents_routes import router as agents_router
router.include_router(skills_router, tags=["skills"])
router.include_router(evolution_router, tags=["evolution"])
router.include_router(agents_router, tags=["agents"])


async def _collect_layered_memory_entries(layered_memory) -> list[dict]:
    items: list[dict] = []
    user_memory = layered_memory.get_user_memory()
    updated_at = user_memory.get("updated_at") or ""
    for category, values in user_memory.items():
        if category == "updated_at" or not isinstance(values, dict):
            continue
        for key, value in values.items():
            items.append({
                "id": f"layered:user:{category}:{key}",
                "key": key,
                "value": str(value),
                "category": f"user/{category}",
                "access_count": 0,
                "created_at": updated_at,
                "updated_at": updated_at,
            })
    profile = layered_memory.get_user_profile()
    if profile.strip():
        items.append({
            "id": "layered:user_profile",
            "key": "USER.md",
            "value": profile.strip(),
            "category": "user_profile",
            "access_count": 0,
            "created_at": "",
            "updated_at": "",
        })
    stats = layered_memory.get_stats()
    for agent_type in stats.get("agent_types", []):
        for entry in layered_memory.get_agent_memory(agent_type):
            key = str(entry.get("key", ""))
            items.append({
                "id": f"layered:agent:{agent_type}:{key}",
                "key": key,
                "value": str(entry.get("value", "")),
                "category": f"agent/{entry.get('category', agent_type)}",
                "access_count": int(entry.get("access_count", 0) or 0),
                "created_at": entry.get("created_at", ""),
                "updated_at": entry.get("updated_at", ""),
            })
    return items


@router.post("/chat")
async def chat(request: ChatRequest):
    # Daily token budget enforcement
    from app.config import settings as _cfg
    if _cfg.daily_token_budget > 0:
        from app.agents.cost_tracker import cost_tracker
        daily = cost_tracker.get_daily_summary()
        today_total = daily.get("total_input_tokens", 0) + daily.get("total_output_tokens", 0)
        if today_total >= _cfg.daily_token_budget:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={
                "error": "daily_budget_exceeded",
                "message": f"今日 Token 已用 {today_total:,}，超出预算 {_cfg.daily_token_budget:,}。请在设置 → Token 预算中调整上限。",
                "used": today_total,
                "budget": _cfg.daily_token_budget,
            })
    if request.thread_id:
        thread = await thread_store.get(request.thread_id)
        if not thread:
            thread = await thread_store.create()
    else:
        thread = await thread_store.create()

    # Snapshot existing messages BEFORE adding the new user message,
    # so agent doesn't see the user message twice.
    history_messages = list(thread.messages)

    user_msg = Message(role="user", content=request.message, thread_id=thread.id)
    await thread_store.add_message(thread.id, user_msg)

    if request.images:
        logger.info(f"Chat received {len(request.images)} images, first image size: {len(request.images[0])} chars")

    async def event_generator():
        full_content = []
        event_queue: asyncio.Queue = asyncio.Queue()
        from app.agents.tool_runtime import permission_requests
        from app.agents.prompt_features import speculation_store

        permission_queue = permission_requests.subscribe(thread.id)
        speculation_queue = speculation_store.subscribe(thread.id)

        async def pump_agent_events():
            try:
                async for event_str in super_agent.handle_message(
                    message=request.message,
                    thread_messages=history_messages,
                    model=request.model,
                    skills=request.skills,
                    tools=request.tools,
                    disabled_tools=request.disabled_tools,
                    mode=request.mode or "standard",
                    thread_id=thread.id,
                    images=request.images,
                    enable_speculation=request.enable_speculation,
                ):
                    await event_queue.put(("agent", event_str))
            except Exception as exc:
                await event_queue.put(("agent", json.dumps({"type": "error", "content": str(exc)})))
            finally:
                await event_queue.put(("agent_done", None))

        async def pump_permission_events():
            try:
                while True:
                    payload = await permission_queue.get()
                    await event_queue.put(("permission", json.dumps(payload)))
            except asyncio.CancelledError:
                return

        async def pump_speculation_events():
            try:
                while True:
                    payload = await speculation_queue.get()
                    await event_queue.put(("speculation", json.dumps(payload)))
            except asyncio.CancelledError:
                return

        agent_task = asyncio.create_task(pump_agent_events())
        permission_task = asyncio.create_task(pump_permission_events())
        speculation_task = asyncio.create_task(pump_speculation_events())

        try:
            agent_finished = False
            done_sent = False
            while True:
                source, event_str = await event_queue.get()
                if source == "agent_done":
                    agent_finished = True
                    permission_task.cancel()
                    speculation_task.cancel()
                    if event_queue.empty():
                        break
                    continue

                event_data = json.loads(event_str)
                if event_data.get("type") == "token":
                    full_content.append(event_data.get("content", ""))
                    yield f"event: token\ndata: {event_str}\n\n"
                elif event_data.get("type") == "plan":
                    yield f"event: plan\ndata: {event_str}\n\n"
                elif event_data.get("type") == "agent_status":
                    yield f"event: agent_status\ndata: {event_str}\n\n"
                elif event_data.get("type") == "agent_summary":
                    yield f"event: agent_summary\ndata: {event_str}\n\n"
                elif event_data.get("type") == "tool_call":
                    yield f"event: tool_call\ndata: {event_str}\n\n"
                elif event_data.get("type") == "tool_result":
                    yield f"event: tool_result\ndata: {event_str}\n\n"
                elif event_data.get("type") == "permission_request":
                    yield f"event: permission_request\ndata: {event_str}\n\n"
                elif event_data.get("type") == "permission_decision":
                    yield f"event: permission_decision\ndata: {event_str}\n\n"
                elif event_data.get("type") == "tool_summary":
                    yield f"event: tool_summary\ndata: {event_str}\n\n"
                elif event_data.get("type") == "prompt_suggestion":
                    yield f"event: prompt_suggestion\ndata: {event_str}\n\n"
                elif event_data.get("type") == "speculation_state":
                    yield f"event: speculation_state\ndata: {event_str}\n\n"
                elif event_data.get("type") == "speculation_hit":
                    yield f"event: speculation_hit\ndata: {event_str}\n\n"
                elif event_data.get("type") == "error":
                    yield f"event: error\ndata: {event_str}\n\n"
                elif event_data.get("type") == "agents_completed":
                    yield f"event: agents_completed\ndata: {event_str}\n\n"
                elif event_data.get("type") == "memory_extracted":
                    yield f"event: memory_extracted\ndata: {event_str}\n\n"
                elif event_data.get("type") in ("reflection_start", "reflection_eval", "reflection_correction_token", "reflection_done"):
                    yield f"event: {event_data['type']}\ndata: {event_str}\n\n"
                elif event_data.get("type") in ("spawn_started", "spawn_completed", "spawn_denied", "spawn_batch_done"):
                    yield f"event: {event_data['type']}\ndata: {event_str}\n\n"
                elif event_data.get("type") == "done":
                    assistant_content = "".join(full_content)
                    assistant_msg = Message(
                        role="assistant",
                        content=assistant_content,
                        thread_id=thread.id,
                    )
                    await thread_store.add_message(thread.id, assistant_msg)

                    try:
                        from app.memory.extract_memories import auto_dream
                        auto_dream.record_session()
                    except Exception as e:
                        logger.debug("Suppressed error in chat: %s", e)

                    done_payload = {"thread_id": thread.id, "type": "done"}
                    if event_data.get("usage"):
                        done_payload["usage"] = event_data["usage"]
                    yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"
                    done_sent = True

                if agent_finished and event_queue.empty():
                    break

            # Safety net: if agent finished but never sent a "done" event,
            # send one now so the frontend never gets stuck spinning.
            if not done_sent:
                logger.warning("Agent finished without sending 'done' event — injecting safety done for thread %s", thread.id)
                assistant_content = "".join(full_content)
                if assistant_content:
                    assistant_msg = Message(role="assistant", content=assistant_content, thread_id=thread.id)
                    await thread_store.add_message(thread.id, assistant_msg)
                done_payload = {"thread_id": thread.id, "type": "done"}
                yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

        finally:
            permission_requests.unsubscribe(thread.id, permission_queue)
            speculation_store.unsubscribe(thread.id, speculation_queue)
            agent_task.cancel()
            permission_task.cancel()
            speculation_task.cancel()
            await asyncio.gather(agent_task, permission_task, speculation_task, return_exceptions=True)

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
            "parent_id": t.parent_id,
            "last_message": " ".join(m.content[:80] for m in t.messages if m.role == "user")[:300] if t.messages else "",
        }
        for t in threads
    ]


@router.post("/threads")
async def create_thread(payload: dict | None = None):
    title = (payload or {}).get("title") or "新对话"
    thread = await thread_store.create(title=title)
    return thread.model_dump(mode="json")


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    thread = await thread_store.get(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread.model_dump(mode="json")


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    success = await thread_store.delete(thread_id)
    if success:
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Thread not found")


@router.get("/threads/{thread_id}/lineage")
async def get_thread_lineage(thread_id: str):
    """Walk the ancestor chain from this thread up to the root."""
    chain = await thread_store.get_lineage(thread_id)
    return {"lineage": chain, "depth": len(chain)}


@router.get("/threads/{thread_id}/children")
async def get_thread_children(thread_id: str):
    """List child threads forked from this thread."""
    children = await thread_store.get_children(thread_id)
    return [
        {
            "id": c.id, "title": c.title, "parent_id": c.parent_id,
            "created_at": c.created_at.isoformat(),
        }
        for c in children
    ]


@router.get("/threads/{thread_id}/trajectory")
async def export_thread_trajectory(thread_id: str):
    exported = await thread_store.export_thread(thread_id)
    if not exported:
        raise HTTPException(status_code=404, detail="Thread not found")
    return exported


@router.post("/trajectories/replay")
async def replay_trajectory(payload: dict):
    trajectory = payload.get("trajectory") if isinstance(payload.get("trajectory"), dict) else payload
    title = payload.get("title") if isinstance(payload, dict) else None
    parent_id = payload.get("parent_id") if isinstance(payload, dict) else None
    thread = await thread_store.import_thread(trajectory, title=title, parent_id=parent_id)
    if not thread:
        raise HTTPException(status_code=400, detail="Invalid trajectory payload")
    return {
        "thread_id": thread.id,
        "title": thread.title,
        "message_count": len(thread.messages),
        "parent_id": thread.parent_id,
        "trajectory": thread.metadata.get("trajectory", {}),
    }


@router.post("/demo/seed")
async def seed_demo(clean: bool = True, dry_run: bool = False):
    from app.demo_seed import run_demo_seed

    return await run_demo_seed(clean=clean, dry_run=dry_run)


@router.post("/threads/{thread_id}/fork")
async def fork_thread(thread_id: str, title: str = None):
    """Fork a new child session from this thread, carrying the compact summary."""
    parent = await thread_store.get(thread_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Thread not found")
    summary = parent.compact_summary or ""
    if not summary and parent.messages:
        # Generate a quick summary from last messages
        from app.agents.context import summarize_messages
        raw = [{"role": m.role, "content": m.content} for m in parent.messages[-10:]]
        try:
            summary = await summarize_messages(raw, max_tokens=300)
        except Exception as e:
            logger.debug("Suppressed error in chat: %s", e)
            summary = f"Forked from: {parent.title}"
    child = await thread_store.fork(thread_id, summary, title=title)
    if not child:
        raise HTTPException(status_code=500, detail="Fork failed")
    return {
        "id": child.id, "title": child.title,
        "parent_id": child.parent_id,
        "compact_summary": child.compact_summary[:200] if child.compact_summary else None,
    }


@router.get("/models")
async def list_models():
    from app.models.provider import llm_provider
    return [
        {
            **m.model_dump(),
            "has_api_key": llm_provider.has_api_key(m.name),
            "api_key_source": llm_provider.get_api_key_source_for_model(m.name),
        }
        for m in llm_provider.list_models()
    ]


@router.get("/providers")
async def list_providers():
    from app.models.provider import llm_provider
    return {"providers": llm_provider.list_supported_providers()}


@router.post("/models")
async def add_model(name: str, display_name: str, model: str, base_url: str = "", api_key_env: str = "OPENAI_API_KEY", provider: str = ""):
    from app.models.provider import llm_provider
    from app.models.schemas import ModelConfig
    config = ModelConfig(name=name, display_name=display_name, model=model, base_url=base_url, api_key_env=api_key_env, provider=provider or "openai")
    llm_provider.add_model(config)
    return {"status": "added", "name": name}


@router.delete("/models/{name}")
async def remove_model(name: str):
    from app.models.provider import llm_provider
    success = llm_provider.remove_model(name)
    if success:
        return {"status": "removed"}
    raise HTTPException(status_code=404, detail="Model not found")


@router.get("/memory")
async def get_memory():
    from app.memory.store import memory_store
    from app.memory.layered_store import layered_memory

    entries = await memory_store.get_all()
    items = [e.model_dump(mode="json") for e in entries]
    layered = await _collect_layered_memory_entries(layered_memory)
    return items + layered


@router.post("/memory")
async def add_memory(key: str, value: str, category: str = "knowledge"):
    from app.memory.store import memory_store
    from app.memory.layered_store import layered_memory

    entry = await memory_store.add(key, value, category)
    try:
        layered_category = "preferences" if category == "preference" else "context"
        layered_memory.set_user_memory(key, value, layered_category)
    except Exception as e:
        logger.debug("Suppressed error in chat: %s", e)
    return entry.model_dump(mode="json")


@router.get("/memory/search")
async def search_memory(query: str = ""):
    from app.memory.store import memory_store
    from app.memory.layered_store import layered_memory

    if not query.strip():
        entries = await memory_store.get_all()
    else:
        entries = await memory_store.search(query)
    items = [e.model_dump(mode="json") for e in entries]
    layered = await _collect_layered_memory_entries(layered_memory)
    if query.strip():
        lowered = query.lower()
        layered = [
            item for item in layered
            if lowered in item.get("key", "").lower()
            or lowered in item.get("value", "").lower()
            or lowered in item.get("category", "").lower()
        ]
    return items + layered


@router.delete("/memory/{entry_id}")
async def delete_memory(entry_id: str):
    from app.memory.store import memory_store
    from app.memory.layered_store import layered_memory

    success = await memory_store.delete(entry_id)
    if success:
        return {"status": "deleted"}
    if entry_id.startswith("layered:user:"):
        parts = entry_id.split(":", 3)
        if len(parts) == 4:
            result = layered_memory.remove_user_memory(parts[3], parts[2])
            if result.get("deleted"):
                return {"status": "deleted"}
            return result
    if entry_id == "layered:user_profile":
        result = layered_memory.update_user_profile("")
        return {"status": "deleted", **result}
    if entry_id.startswith("layered:agent:"):
        parts = entry_id.split(":", 3)
        if len(parts) == 4:
            result = layered_memory.remove_agent_memory(parts[2], parts[3])
            if result.get("deleted"):
                return {"status": "deleted"}
            return result
    raise HTTPException(status_code=404, detail="Entry not found")


_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
_ALLOWED_UPLOAD_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".toml",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
    ".java", ".c", ".cpp", ".h", ".go", ".rs", ".rb", ".php", ".swift",
    ".sh", ".sql", ".r", ".lua", ".dart", ".kt", ".scala", ".zig",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".7z",
    ".wav", ".mp3", ".mp4", ".webm",
    ".env", ".gitignore", ".dockerignore",
}

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    upload_dir = os.path.join("./data", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "file")[1].lower()
    if ext and ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type '{ext}' not allowed")
    filepath = os.path.join(upload_dir, f"{file_id}{ext}")
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {_MAX_UPLOAD_BYTES // 1024 // 1024}MB)")
    with open(filepath, "wb") as f:
        f.write(content)
    return {
        "id": file_id,
        "filename": file.filename,
        "size": len(content),
        "path": filepath,
    }


@router.post("/sandbox/execute")
async def sandbox_execute(request: Request, code: str, language: str = "python", timeout: int = 30):
    from app.config import settings as _cfg
    if not _cfg.sandbox_enabled:
        raise HTTPException(status_code=403, detail="Sandbox execution is disabled (set sandbox_enabled=true in config)")
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", ""):
        raise HTTPException(status_code=403, detail="Sandbox execution is only allowed from localhost")
    if language in ("python", "python3"):
        result = await runtime_manager.execute_python(code, timeout)
    elif language in ("javascript", "js", "node"):
        result = await runtime_manager.execute_javascript(code, timeout)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")
    return result


@router.get("/workspace/{thread_id}/files")
async def list_workspace_files(thread_id: str, path: str = "."):
    result = await runtime_manager.list_files(path, thread_id=thread_id)
    if not result["success"]:
        return {
            "error": result.get("error", "Failed to list files"),
            "hint": result.get("hint"),
            "backend": result.get("backend"),
            "requested_backend": result.get("requested_backend"),
        }
    return result


@router.get("/workspace/{thread_id}/read")
async def read_workspace_file(thread_id: str, path: str):
    result = await runtime_manager.read_file(path, thread_id=thread_id)
    if not result["success"]:
        return {
            "error": result.get("error", "Failed to read file"),
            "hint": result.get("hint"),
            "backend": result.get("backend"),
            "requested_backend": result.get("requested_backend"),
        }
    return result


@router.get("/workspace/{thread_id}/download/{file_path:path}")
async def download_workspace_file(thread_id: str, file_path: str):
    from fastapi.responses import FileResponse
    full_path = runtime_manager.resolve_workspace_path(thread_id, file_path)
    if not full_path:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(full_path, filename=os.path.basename(file_path))


@router.get("/outputs/{thread_id}/download/{file_path:path}")
async def download_output_file(thread_id: str, file_path: str):
    from fastapi.responses import FileResponse
    full_path = runtime_manager.resolve_outputs_path(thread_id, file_path)
    if not full_path:
        raise HTTPException(status_code=403, detail="Path traversal not allowed")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(full_path, filename=os.path.basename(file_path))


@router.get("/screenshots/{filename}")
async def get_screenshot_file(filename: str):
    from fastapi.responses import FileResponse
    if not filename.startswith("screenshot_") or not filename.endswith(".png") or os.path.basename(filename) != filename:
        raise HTTPException(status_code=400, detail="Invalid screenshot filename")
    full_path = os.path.join("/tmp", filename)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(full_path, media_type="image/png", filename=filename)


@router.get("/runtimes")
async def list_runtime_backends():
    return {
        "default_backend": runtime_manager.default_backend_name,
        "routing_defaults": runtime_manager.routing_policy_defaults(),
        "observability": runtime_manager.observability_snapshot(),
        "backends": runtime_manager.list_backends(),
    }


@router.post("/runtimes/{backend_name}/prewarm")
async def prewarm_runtime_backend(backend_name: str):
    return runtime_manager.prewarm_backend(backend_name)


@router.get("/threads/{thread_id}/runtime")
async def get_thread_runtime(thread_id: str):
    return runtime_manager.get_thread_runtime(thread_id)


@router.post("/threads/{thread_id}/runtime")
async def set_thread_runtime(thread_id: str, payload: dict):
    backend = str(payload.get("backend", "")).strip()
    if not backend:
        raise HTTPException(status_code=400, detail="backend is required")
    policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else None
    return runtime_manager.set_thread_backend(thread_id, backend, policy=policy)


@router.delete("/threads/{thread_id}/runtime")
async def clear_thread_runtime(thread_id: str):
    return runtime_manager.clear_thread_backend(thread_id)


@router.get("/task-evals/overview")
async def get_task_eval_overview():
    return {
        **seed_dataset_overview(),
        "runs": list_task_eval_runs(limit=10),
    }


@router.get("/task-evals/runs")
async def get_task_eval_runs(limit: int = 20):
    return {"runs": list_task_eval_runs(limit=limit)}


@router.post("/task-evals/runs")
async def create_task_eval_run(payload: dict):
    dataset = str(payload.get("dataset") or payload.get("dataset_path") or "").strip() or None
    raw_case_ids = payload.get("case_ids") or payload.get("caseIds") or []
    case_ids = [str(item).strip() for item in raw_case_ids if str(item).strip()] if isinstance(raw_case_ids, list) else []
    raw_skills = payload.get("skills") or []
    raw_allowed_tools = payload.get("allowed_tools") or payload.get("allowedTools") or []
    raw_disallowed_tools = payload.get("disallowed_tools") or payload.get("disallowedTools") or []
    agent_config = TaskEvalAgentConfig(
        mode=str(payload.get("mode") or "standard").strip() or "standard",
        model=str(payload.get("model") or "").strip() or None,
        skills=[str(item).strip() for item in raw_skills if str(item).strip()] if isinstance(raw_skills, list) else [],
        allowed_tools=[str(item).strip() for item in raw_allowed_tools if str(item).strip()] if isinstance(raw_allowed_tools, list) else None,
        disallowed_tools=[str(item).strip() for item in raw_disallowed_tools if str(item).strip()] if isinstance(raw_disallowed_tools, list) else [],
        enable_speculation=payload.get("enable_speculation") if isinstance(payload.get("enable_speculation"), bool) else payload.get("enableSpeculation") if isinstance(payload.get("enableSpeculation"), bool) else None,
        thread_id_prefix=str(payload.get("thread_id_prefix") or payload.get("threadIdPrefix") or "task-eval").strip() or "task-eval",
    )
    runner = TaskEvalRunner(dataset_path=dataset)
    run = await runner.arun(
        lambda case: evaluate_task_case_with_agent(case, agent_config),
        case_ids=case_ids or None,
        label=str(payload.get("label") or "").strip(),
        persist=True,
    )
    return {
        "run": run.to_dict(),
        "report": render_task_eval_report(run, runner._selected_cases(case_ids or None)),
    }


@router.get("/task-evals/runs/{run_id}")
async def get_task_eval_run(run_id: str):
    run = load_task_eval_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Task eval run not found: {run_id}")
    return run.to_dict()


@router.get("/task-evals/runs/{run_id}/report")
async def get_task_eval_run_report(run_id: str):
    run = load_task_eval_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Task eval run not found: {run_id}")
    cases = load_task_cases(run.dataset_path or None)
    return {
        "run_id": run.run_id,
        "report": render_task_eval_report(run, cases),
    }


@router.post("/preview/html")
async def preview_html(html: str):
    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


# --- MCP Server Management ---
@router.get("/mcp/servers")
async def list_mcp_servers():
    from app.skills.mcp import mcp_registry
    return mcp_registry.list_servers()


@router.post("/mcp/servers")
async def add_mcp_server(payload: dict):
    """Register an MCP server.

    Payload (JSON body):
      - name: str (required)
      - transport: "http" | "stdio" (default "http")
      - url: str (http transport)
      - api_key: str | null (http transport)
      - command: str (stdio transport, e.g. "node" or "uvx")
      - args: list[str] (stdio transport)
      - env: dict[str, str] (stdio transport)
      - enabled: bool (default true)
    """
    from app.skills.mcp import mcp_registry, MCPServerConfig
    try:
        config = MCPServerConfig(**payload)
    except Exception as e:
        return {"ok": False, "message": f"Invalid config: {e}"}
    mcp_registry.register(config)
    try:
        await mcp_registry.discover_all()
    except Exception as e:
        return {"ok": True, "status": "registered",
                "name": config.name, "discover_error": str(e)}
    return {"ok": True, "status": "registered", "name": config.name}


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


@router.get("/mcp/prompts")
async def list_mcp_prompts():
    from app.skills.mcp import mcp_registry
    return mcp_registry.list_all_prompts()


@router.post("/mcp/prompts/call")
async def call_mcp_prompt(payload: dict):
    from app.skills.mcp import mcp_registry
    return await mcp_registry.call_prompt(
        payload.get("server_name", ""),
        payload.get("prompt_name", ""),
        payload.get("arguments", {}),
    )


@router.get("/mcp/resources")
async def list_mcp_resources():
    from app.skills.mcp import mcp_registry
    return mcp_registry.list_all_resources()


@router.post("/mcp/resources/read")
async def read_mcp_resource(payload: dict):
    from app.skills.mcp import mcp_registry
    return await mcp_registry.read_resource(
        payload.get("server_name", ""),
        payload.get("uri", ""),
    )


# --- Knowledge Base / RAG ---
@router.get("/knowledge")
async def list_knowledge():
    from app.rag.store import knowledge_base
    return knowledge_base.list_documents()


_KNOWLEDGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_KNOWLEDGE_ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".html", ".py", ".js", ".ts", ".pdf"}

@router.post("/knowledge/upload")
async def upload_knowledge(file: UploadFile = File(...)):
    from app.rag.store import knowledge_base
    ext = os.path.splitext(file.filename or "file")[1].lower()
    if ext and ext not in _KNOWLEDGE_ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Knowledge upload: file type '{ext}' not allowed")
    content = await file.read()
    if len(content) > _KNOWLEDGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"Knowledge file too large (max {_KNOWLEDGE_MAX_BYTES // 1024 // 1024}MB)")
    text = content.decode("utf-8", errors="replace")
    doc_id = knowledge_base.add_document(file.filename or "untitled", text)
    return {"doc_id": doc_id, "name": file.filename, "size": len(text)}


@router.post("/knowledge/text")
async def add_knowledge_text(name: str, content: str):
    from app.rag.store import knowledge_base
    doc_id = knowledge_base.add_document(name, content)
    return {"doc_id": doc_id, "name": name, "size": len(content)}


@router.delete("/knowledge/{doc_id}")
async def delete_knowledge(doc_id: str):
    from app.rag.store import knowledge_base
    if knowledge_base.remove_document(doc_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Document not found")


@router.get("/knowledge/search")
async def search_knowledge(query: str, top_k: int = 5):
    from app.rag.store import knowledge_base
    return knowledge_base.search(query, top_k)


# --- Task Management ---
@router.get("/tasks")
async def list_tasks(agent_id: str | None = None):
    from app.agents.task_manager import task_manager
    return task_manager.list_tasks(agent_id)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    from app.agents.task_manager import task_manager
    task = task_manager.get_task(task_id)
    if task:
        return task.to_dict()
    raise HTTPException(status_code=404, detail="Task not found")


@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    from app.agents.task_manager import task_manager
    if task_manager.cancel_task(task_id):
        return {"status": "cancelled"}
    raise HTTPException(status_code=404, detail="Task not found or not running")


@router.get("/tools")
async def list_all_tools(include_deferred: bool = True, enable_tool_search: bool = True):
    from app.agents.tools import get_all_tools
    from app.agents.tool_runtime import describe_tool

    tools = get_all_tools(include_deferred=include_deferred, enable_tool_search=enable_tool_search, wrap=False)
    return [describe_tool(t) for t in tools]


@router.get("/permissions/rules")
async def get_permission_rules():
    from app.agents.tool_runtime import permission_rules

    return permission_rules.get_rules()


@router.post("/permissions/rules")
async def set_permission_rules(payload: dict):
    from app.agents.tool_runtime import permission_rules

    permission_rules.set_rules(payload)
    return permission_rules.get_rules()


@router.get("/permissions/pending")
async def list_pending_permissions(thread_id: str | None = None):
    from app.agents.tool_runtime import permission_requests

    return {"requests": permission_requests.get_pending(thread_id)}


@router.post("/permissions/{request_id}/approve")
async def approve_permission_request(request_id: str, note: str = ""):
    from app.agents.tool_runtime import permission_requests

    result = await permission_requests.resolve_request(request_id, approve=True, note=note)
    if result is None:
        raise HTTPException(status_code=404, detail="Permission request not found")
    return result


@router.post("/permissions/{request_id}/deny")
async def deny_permission_request(request_id: str, note: str = ""):
    from app.agents.tool_runtime import permission_requests

    result = await permission_requests.resolve_request(request_id, approve=False, note=note)
    if result is None:
        raise HTTPException(status_code=404, detail="Permission request not found")
    return result


@router.get("/security/policy")
async def get_security_policy():
    from app.security.policy import (
        app_environment,
        disabled_tools,
        is_production_environment,
        permission_matrix,
        production_policy_enabled,
    )

    return {
        "environment": app_environment(),
        "production": is_production_environment(),
        "production_policy_enabled": production_policy_enabled(),
        "disabled_tools": sorted(disabled_tools()),
        "matrix": permission_matrix(),
    }


@router.get("/security/audit")
async def list_security_audit(
    limit: int = 100,
    thread_id: str | None = None,
    tool: str | None = None,
    decision: str | None = None,
):
    from app.security.audit import security_audit_log

    return {
        "events": security_audit_log.list_events(
            limit=limit,
            thread_id=thread_id,
            tool=tool,
            decision=decision,
        )
    }


@router.get("/speculation/{thread_id}")
async def get_thread_speculation(thread_id: str):
    from app.agents.prompt_features import get_speculation

    record = get_speculation(thread_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Speculation not found")
    return record


@router.delete("/speculation/{thread_id}")
async def clear_thread_speculation(thread_id: str):
    from app.agents.prompt_features import clear_speculation

    if not clear_speculation(thread_id):
        raise HTTPException(status_code=404, detail="Speculation not found")
    return {"status": "cleared"}


@router.get("/speculation/{thread_id}/changes")
async def get_thread_speculation_changes(thread_id: str):
    from app.agents.prompt_features import get_speculation_changes

    record = get_speculation_changes(thread_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Speculation not found")
    return record


@router.get("/speculation/{thread_id}/diff")
async def get_thread_speculation_diff(thread_id: str):
    from app.agents.prompt_features import get_speculation_diff

    record = get_speculation_diff(thread_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Speculation not found")
    return record


@router.post("/speculation/{thread_id}/accept")
async def accept_thread_speculation(thread_id: str, payload: dict | None = None):
    from app.agents.prompt_features import accept_speculation

    paths = None
    hunks = None
    if isinstance(payload, dict):
        raw_paths = payload.get("paths")
        if isinstance(raw_paths, list):
            paths = [str(item).strip() for item in raw_paths if str(item).strip()]
        raw_hunks = payload.get("hunks")
        if isinstance(raw_hunks, list):
            hunks = [entry for entry in raw_hunks if isinstance(entry, dict)]
    record = accept_speculation(thread_id, paths=paths, hunks=hunks)
    if record is None:
        raise HTTPException(status_code=404, detail="Speculation not found")
    return record


@router.get("/policy-limits")
async def get_policy_limits():
    from app.agents.tool_runtime import policy_limits

    return {"restrictions": policy_limits.get_restrictions()}


@router.post("/policy-limits")
async def set_policy_limits(payload: dict):
    from app.agents.tool_runtime import policy_limits

    policy_limits.set_restrictions(payload)
    return {"restrictions": policy_limits.get_restrictions()}


@router.get("/magic-docs")
async def list_magic_docs():
    from app.agents.magic_docs import magic_docs

    return {"docs": magic_docs.list_docs()}


@router.post("/magic-docs/register")
async def register_magic_doc(file_path: str):
    from app.agents.magic_docs import magic_docs

    result = magic_docs.register(file_path)
    if not result:
        raise HTTPException(status_code=400, detail="Magic doc header not found or feature disabled")
    return result


@router.post("/magic-docs/sync")
async def sync_magic_doc(file_path: str):
    from app.agents.magic_docs import magic_docs

    return magic_docs.sync_doc(file_path)


@router.post("/magic-docs/auto-sync")
async def set_magic_doc_auto_sync(file_path: str, enabled: bool = True):
    from app.agents.magic_docs import magic_docs

    return magic_docs.set_auto_sync(file_path, enabled)


# --- Cost Tracking ---
@router.get("/cost")
async def get_cost_summary():
    from app.agents.cost_tracker import cost_tracker
    return cost_tracker.get_session_summary()


@router.get("/statusline")
async def get_statusline():
    from app.agents.context import get_compact_state
    from app.agents.cost_tracker import cost_tracker
    from app.models.provider import llm_provider
    from app.skills.mcp import mcp_registry

    threads = await thread_store.list_threads()
    compact = get_compact_state()
    cost = cost_tracker.get_session_summary()
    active_servers = [s for s in mcp_registry.list_servers() if s.get("enabled")]
    models = llm_provider.list_models()
    return {
        "threads": len(threads),
        "models": len(models),
        "mcp_servers": len(active_servers),
        "compact_level": compact.get("level", "none"),
        "token_usage_pct": compact.get("token_usage_pct", 0),
        "session_requests": cost.get("session_requests", 0),
        "total_cost_usd": cost.get("total_cost_usd", 0),
        "budget_usd": cost.get("budget_usd"),
        "is_over_budget": cost.get("is_over_budget", False),
    }


@router.get("/cost/today")
async def get_today_cost():
    from app.agents.cost_tracker import cost_tracker
    return cost_tracker.get_daily_summary()


@router.get("/cost/history")
async def get_cost_history(limit: int = 100):
    from app.agents.cost_tracker import cost_tracker
    records = cost_tracker.get_history(limit=limit) if hasattr(cost_tracker, "get_history") else []
    return {"records": records}


@router.get("/cost/models")
async def get_cost_by_model():
    from app.agents.cost_tracker import cost_tracker
    return cost_tracker.get_model_breakdown() if hasattr(cost_tracker, "get_model_breakdown") else {}


@router.get("/cost/budget")
async def get_budget():
    from app.agents.cost_tracker import cost_tracker
    return cost_tracker.get_budget_status()


@router.post("/cost/budget")
async def set_budget(max_usd: float = 0.0):
    from app.agents.cost_tracker import cost_tracker
    cost_tracker.set_budget(max_usd)
    return {"status": "ok", "max_budget_usd": max_usd}


# --- Agent Definitions ---
@router.get("/agents")
async def list_agent_definitions():
    from app.agents.orchestrator import BUILT_IN_AGENTS
    return {
        name: {
            "agent_type": d.agent_type,
            "when_to_use": d.when_to_use,
            "tools": d.tools,
            "disallowed_tools": d.disallowed_tools,
            "max_turns": d.max_turns,
            "is_read_only": d.is_read_only,
        }
        for name, d in BUILT_IN_AGENTS.items()
    }


# --- Bash Safety ---
@router.get("/safety/bash")
async def check_bash_command(command: str):
    from app.agents.orchestrator import check_bash_safety
    return check_bash_safety(command)


@router.post("/safety/classify")
async def classify_tool_risk(payload: dict):
    from app.agents.safety_classifier import get_risk_summary
    tool_name = payload.get("tool_name", "")
    args = payload.get("args", {})
    context = payload.get("context")
    return get_risk_summary(tool_name, args, context)


# --- Layered Memory ---
@router.get("/memory/stats")
async def memory_stats():
    from app.memory.layered_store import layered_memory
    from app.memory.extract_memories import auto_dream, memory_extractor
    return {
        **layered_memory.get_stats(),
        "extraction": memory_extractor.get_stats(),
        "dream": auto_dream.get_state(),
        "note": "记忆分为项目记忆、用户记忆、Agent 记忆和会话记忆。自动抽取会写入用户/Agent/会话记忆。",
    }


@router.get("/memory/layered")
async def list_layered_memory():
    from app.memory.layered_store import layered_memory
    stats = layered_memory.get_stats()
    agent_memory = {}
    for agent_type in stats.get("agent_types", []):
        agent_memory[agent_type] = layered_memory.get_agent_memory(agent_type)
    return {
        "project": layered_memory.get_project_memory(),
        "user_profile": layered_memory.get_user_profile(),
        "user": layered_memory.get_user_memory(),
        "agent": agent_memory,
        "session": layered_memory.list_session_memories(),
        "auto": layered_memory.list_auto_memories(),
        "stats": stats,
    }


@router.get("/memory/project")
async def get_project_memory():
    from app.memory.layered_store import layered_memory
    return {"content": layered_memory.get_project_memory()}


@router.post("/memory/project")
async def update_project_memory(content: str = "", entry: str = ""):
    from app.memory.layered_store import layered_memory
    if entry:
        return layered_memory.append_project_memory(entry)
    return layered_memory.update_project_memory(content)


@router.get("/memory/user")
async def get_user_memory(user_id: str = "default"):
    from app.memory.layered_store import layered_memory
    return layered_memory.get_user_memory(user_id)


@router.post("/memory/user")
async def set_user_memory(key: str, value: str, category: str = "context", user_id: str = "default"):
    from app.memory.layered_store import layered_memory
    return layered_memory.set_user_memory(key, value, category, user_id)


@router.get("/memory/agent/{agent_type}")
async def get_agent_memory(agent_type: str):
    from app.memory.layered_store import layered_memory
    return layered_memory.get_agent_memory(agent_type)


@router.post("/memory/agent/{agent_type}")
async def add_agent_memory(agent_type: str, key: str, value: str, category: str = "learned"):
    from app.memory.layered_store import layered_memory
    return layered_memory.add_agent_memory(agent_type, key, value, category)


@router.get("/memory/profile")
async def get_user_profile():
    from app.memory.layered_store import layered_memory
    return {"content": layered_memory.get_user_profile()}


@router.post("/memory/profile")
async def update_user_profile(content: str = "", entry: str = ""):
    from app.memory.layered_store import layered_memory
    if entry:
        return layered_memory.append_user_profile(entry)
    return layered_memory.update_user_profile(content)


@router.get("/memory/context")
async def build_memory_context(query: str = "", agent_type: str = "", user_id: str = "default", session_id: str = ""):
    from app.memory.layered_store import layered_memory
    return {"context": layered_memory.build_context(query, agent_type, user_id, session_id)}


# --- Memory replace/remove/capacity ---
@router.post("/memory/replace")
async def memory_replace(payload: dict):
    from app.memory.layered_store import layered_memory
    return layered_memory.replace_project_memory(payload.get("old_text", ""), payload.get("new_text", ""))


@router.post("/memory/remove")
async def memory_remove(payload: dict):
    from app.memory.layered_store import layered_memory
    return layered_memory.remove_project_memory(payload.get("old_text", ""))


@router.get("/memory/capacity")
async def memory_capacity():
    from app.memory.layered_store import layered_memory
    return layered_memory.get_memory_capacity()


# --- Auto Memory ---
@router.post("/memory/auto")
async def save_auto_memory(payload: dict):
    from app.memory.layered_store import layered_memory
    return layered_memory.save_auto_memory(payload.get("topic", ""), payload.get("content", ""))


@router.get("/memory/auto")
async def list_auto_memories():
    from app.memory.layered_store import layered_memory
    return {"topics": layered_memory.list_auto_memories()}


@router.get("/memory/auto/{topic}")
async def get_auto_memory(topic: str):
    from app.memory.layered_store import layered_memory
    return {"topic": topic, "content": layered_memory.get_auto_memory(topic)}


# --- Context / Compact ---
@router.get("/context/compact")
async def get_compact_state():
    from app.agents.context import get_compact_state
    return get_compact_state()


@router.get("/context/tokens")
async def estimate_context_tokens(text: str = ""):
    from app.agents.context import estimate_message_tokens, calculate_token_warning_state
    tokens = estimate_message_tokens([{"content": text}]) if text else 0
    return {**calculate_token_warning_state(tokens), "estimated_tokens": tokens}


# --- Memory Extraction & Dream ---
@router.get("/memory/extraction/stats")
async def extraction_stats():
    from app.memory.extract_memories import memory_extractor
    return memory_extractor.get_stats()


@router.post("/memory/extract")
async def force_extract_memories(session_id: str = "manual"):
    from app.memory.extract_memories import memory_extractor
    # Get recent messages from last thread
    return {"note": "Use force=true with conversation messages for manual extraction", "stats": memory_extractor.get_stats()}


@router.get("/memory/dream/state")
async def dream_state():
    from app.memory.extract_memories import auto_dream
    return auto_dream.get_state()


@router.post("/memory/dream/consolidate")
async def run_dream():
    from app.memory.extract_memories import auto_dream
    result = await auto_dream.consolidate()
    return result


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


@router.get("/channels/{channel_type}/sessions")
async def list_channel_sessions(channel_type: str):
    from app.skills.channels import channel_manager
    return {"channel_type": channel_type, "sessions": channel_manager.list_sessions(channel_type)}


@router.get("/channels/{channel_type}/status")
async def channel_status(channel_type: str):
    from app.skills.channels import channel_manager
    return channel_manager.get_channel_status(channel_type)


@router.post("/channels/{channel_type}/messages")
async def channel_message(channel_type: str, payload: dict):
    from app.skills.channels import ChannelMessageRequest, channel_manager
    request = ChannelMessageRequest(**payload)
    return await channel_manager.handle_message(channel_type, request)


@router.post("/channels/telegram/webhook")
async def telegram_webhook(payload: dict, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    from app.skills.telegram_transport import telegram_transport
    return await telegram_transport.handle_webhook(payload, x_telegram_bot_api_secret_token)


# --- File History ---
@router.get("/threads/{thread_id}/file-history")
async def file_history(thread_id: str, path: str | None = None, limit: int = 50):
    """Get file change history for a thread, optionally filtered by path."""
    entries = runtime_manager.get_file_history(thread_id, path=path, limit=limit)
    try:
        from datetime import datetime
        from app.local.gateway import local_gateway
        local_entries = []
        for item in local_gateway.get_audit_log(limit=limit, thread_id=thread_id):
            action = item.get("action", "")
            params = item.get("params_summary", {})
            target = params.get("path") or params.get("app_name") or action
            if path and path not in str(target):
                continue
            local_entries.append({
                "timestamp": datetime.fromtimestamp(float(item.get("timestamp", 0))).isoformat(),
                "path": str(target),
                "action": f"local_{action}",
                "old_size": 0,
                "new_size": 0,
                "diff": "",
                "success": item.get("success", False),
                "client_id": item.get("client_id", ""),
                "source": "local",
            })
        entries = entries + local_entries
        entries.sort(key=lambda entry: str(entry.get("timestamp", "")))
    except Exception as e:
        logger.debug("Suppressed error in chat: %s", e)
    return {"thread_id": thread_id, "count": len(entries), "entries": entries[-limit:]}


