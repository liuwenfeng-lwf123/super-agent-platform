from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Request
from app.agents.store import thread_store
from app.local.agent import LocalAgent
from app.local.gateway import LocalClient, local_gateway
from app.local.editor_state import editor_state_store
from app.models.schemas import Message, ChatRequest
from fastapi.responses import StreamingResponse
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

local_router = APIRouter(prefix="/local", tags=["Local Mode"])
local_agent = LocalAgent()
LOCAL_PERMISSION_TOOLS = {
    "local_execute_bash",
    "local_read_file",
    "local_write_file",
    "local_list_files",
    "local_execute_python",
    "local_open_app",
    "local_get_system_info",
    "local_upload_to_workspace",
    "local_download_from_workspace",
    "browser_open",
    "screenshot",
    "local_read_clipboard",
    "local_write_clipboard",
    "local_edit_file",
    "local_undo_edit",
    "local_search_code",
    "local_git",
    "local_project_index",
    "local_send_notification",
    "local_manage_window",
    "local_create_schedule",
    "local_list_schedules",
    "local_delete_schedule",
}
local_ws_router = APIRouter(tags=["local-ws"])


@local_ws_router.websocket("/ws/local-client")
async def ws_local_client(websocket: WebSocket):
    from app.security.auth import verify_ws_token
    if not verify_ws_token(websocket):
        await websocket.close(code=4003, reason="Invalid or missing token")
        return
    await websocket.accept()
    client_id = None

    try:
        init_msg = await websocket.receive_json()
        if init_msg.get("type") != "register":
            await websocket.close(code=4001, reason="Must register first")
            return

        client_id = init_msg.get("client_id", "unknown")
        info = init_msg.get("info", {})
        client = LocalClient(client_id=client_id, websocket=websocket, info=info)
        local_gateway.register_client(client)

        await websocket.send_json({
            "type": "registered",
            "client_id": client_id,
            "message": "Connected to TianGongFlow Local Mode",
        })

        while True:
            try:
                data = await websocket.receive_json()
            except Exception as e:
                logger.debug("Suppressed error in local: %s", e)
                break

            client.touch()
            msg_type = data.get("type")

            if msg_type == "response":
                request_id = data.get("request_id")
                result = data.get("result", {})
                client.handle_response(request_id, result)

            elif msg_type == "rejection":
                request_id = data.get("request_id")
                reason = data.get("reason", "No reason provided")
                client.handle_rejection(request_id, reason)

            elif msg_type == "stream_output":
                request_id = data.get("request_id")
                stream = data.get("stream", "stdout")
                chunk_data = data.get("data", "")
                client.handle_stream_output(request_id, stream, chunk_data)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "update_info":
                client.info.update(data.get("info", {}))

            elif msg_type == "update_editor_state":
                thread_id = data.get("thread_id")
                state = data.get("state")
                if isinstance(thread_id, str) and thread_id.strip() and isinstance(state, dict):
                    editor_state_store.update_state(thread_id, state, client_id=client.client_id)
                    await websocket.send_json({
                        "type": "editor_state_updated",
                        "thread_id": thread_id,
                    })
                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": "update_editor_state requires thread_id and state object",
                    })

    except WebSocketDisconnect:
        logger.info(f"Local client {client_id} disconnected")
    except Exception as e:
        logger.error(f"Local client {client_id} WebSocket error: {e}", exc_info=True)
    finally:
        if client_id:
            local_gateway.unregister_client(client_id)


@local_router.get("/clients")
async def list_local_clients():
    return local_gateway.list_clients()


@local_router.post("/clients/{client_id}/auto-approve")
async def set_auto_approve(client_id: str, enabled: bool = True):
    local_gateway.set_auto_approve(client_id, enabled)
    return {"status": "updated", "client_id": client_id, "auto_approve": enabled}


@local_router.post("/clients/{client_id}/tool-permission")
async def set_tool_permission(client_id: str, tool: str, enabled: bool = True):
    if tool not in LOCAL_PERMISSION_TOOLS:
        raise HTTPException(status_code=400, detail=f"Unsupported local permission tool: {tool}")
    local_gateway.set_tool_auto_approve(client_id, tool, enabled)
    return {"status": "updated", "client_id": client_id, "tool": tool, "auto_approve": enabled}


@local_router.post("/bind-thread")
async def bind_thread(thread_id: str, client_id: str):
    local_gateway.bind_thread(thread_id, client_id)
    return {"status": "bound", "thread_id": thread_id, "client_id": client_id}


@local_router.post("/unbind-thread")
async def unbind_thread(thread_id: str):
    local_gateway.unbind_thread(thread_id)
    return {"status": "unbound", "thread_id": thread_id}


@local_router.get("/audit")
async def get_audit_log(limit: int = 100, thread_id: str = ""):
    return local_gateway.get_audit_log(limit, thread_id=thread_id)


@local_router.get("/tool-stats")
async def get_tool_stats():
    """Return per-tool usage counts from audit log for smart auto-approve suggestions."""
    entries = local_gateway.get_audit_log(limit=500)
    stats: dict[str, dict] = {}
    for e in entries:
        action = e.get("action", "")
        tool_key = f"local_{action}" if not action.startswith("local_") else action
        if tool_key not in stats:
            stats[tool_key] = {"tool": tool_key, "total": 0, "success": 0}
        stats[tool_key]["total"] += 1
        if e.get("success"):
            stats[tool_key]["success"] += 1
    return list(stats.values())


@local_router.get("/shortcuts")
async def list_shortcuts():
    from app.local.shortcuts import list_shortcuts
    return list_shortcuts()


@local_router.post("/shortcuts")
async def create_shortcut(request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    description = body.get("description", "").strip()
    steps = body.get("steps", [])
    if not name or not steps:
        raise HTTPException(status_code=400, detail="name and steps are required")
    from app.local.shortcuts import save_shortcut
    save_shortcut(name, description, steps)
    return {"status": "saved", "name": name}


@local_router.delete("/shortcuts/{name}")
async def remove_shortcut(name: str):
    from app.local.shortcuts import delete_shortcut
    if delete_shortcut(name):
        return {"status": "deleted", "name": name}
    raise HTTPException(status_code=404, detail="Shortcut not found")


@local_router.get("/schedules")
async def list_schedules():
    from app.local.scheduler import list_schedules
    return list_schedules()


@local_router.post("/schedules")
async def create_schedule(request: Request):
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    from app.local.scheduler import add_schedule
    result = add_schedule(
        message=message,
        run_at=body.get("run_at"),
        interval_minutes=body.get("interval_minutes"),
        description=body.get("description", ""),
        thread_id=body.get("thread_id", ""),
    )
    return result


@local_router.delete("/schedules/{schedule_id}")
async def remove_schedule(schedule_id: str):
    from app.local.scheduler import remove_schedule
    if remove_schedule(schedule_id):
        return {"status": "deleted", "id": schedule_id}
    raise HTTPException(status_code=404, detail="Schedule not found")


@local_router.post("/chat")
async def local_chat(request: ChatRequest):
    logger.info(f"Local chat request: thread_id={request.thread_id}, mode={request.mode}, message={request.message[:50]}")

    thread_id = request.thread_id
    if thread_id:
        thread = await thread_store.get(thread_id)
        if not thread:
            thread = await thread_store.create()
    else:
        thread = await thread_store.create()

    # Snapshot existing messages BEFORE adding the new user message,
    # so agent.py can safely append HumanMessage without duplication.
    history_messages = list(thread.messages)

    user_msg = Message(role="user", content=request.message, thread_id=thread.id)
    await thread_store.add_message(thread.id, user_msg)

    async def event_generator():
        full_content = []
        event_queue: asyncio.Queue = asyncio.Queue()
        from app.agents.tool_runtime import permission_requests

        permission_queue = permission_requests.subscribe(thread.id)

        async def pump_agent_events():
            try:
                async for event_str in local_agent.handle_message(
                    message=request.message,
                    thread_messages=history_messages,
                    model=request.model,
                    tools=request.tools,
                    disabled_tools=request.disabled_tools,
                    thread_id=thread.id,
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

        async def pump_heartbeat():
            """Send periodic SSE comments to force proxy flush."""
            try:
                while True:
                    await asyncio.sleep(1)
                    await event_queue.put(("heartbeat", None))
            except asyncio.CancelledError:
                return

        agent_task = asyncio.create_task(pump_agent_events())
        permission_task = asyncio.create_task(pump_permission_events())
        heartbeat_task = asyncio.create_task(pump_heartbeat())

        try:
            agent_finished = False
            done_sent = False
            while True:
                source, event_str = await event_queue.get()
                if source == "heartbeat":
                    yield ": heartbeat\n\n"
                    continue
                if source == "agent_done":
                    agent_finished = True
                    permission_task.cancel()
                    heartbeat_task.cancel()
                    if event_queue.empty():
                        break
                    continue

                event_data = json.loads(event_str)
                if event_data.get("type") == "token":
                    full_content.append(event_data.get("content", ""))
                    yield f"event: token\ndata: {event_str}\n\n"
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
                        from app.memory.extract_memories import memory_extractor
                        all_msgs = [
                            {"role": m.role, "content": m.content}
                            for m in history_messages
                        ] + [
                            {"role": "user", "content": request.message},
                            {"role": "assistant", "content": assistant_content},
                        ]
                        extracted = await memory_extractor.maybe_extract(all_msgs, session_id=thread.id)
                        if extracted:
                            yield f"event: memory_extracted\ndata: {json.dumps({'type': 'memory_extracted', 'count': len(extracted), 'memories': [m['key'] for m in extracted]})}\n\n"
                    except Exception as e:
                        logger.debug("Suppressed error in local: %s", e)

                    try:
                        from app.memory.extract_memories import auto_dream
                        auto_dream.record_session()
                    except Exception as e:
                        logger.debug("Suppressed error in local: %s", e)

                    logger.info("Sending done event for thread %s (content_len=%d)", thread.id, len(assistant_content))
                    yield f"event: done\ndata: {json.dumps({'thread_id': thread.id, 'type': 'done'})}\n\n"
                    done_sent = True
                if agent_finished and event_queue.empty():
                    break

            # Safety net: always send done so frontend never gets stuck
            if not done_sent:
                logger.warning("Local agent finished without 'done' — injecting safety done for thread %s", thread.id)
                assistant_content = "".join(full_content)
                if assistant_content:
                    await thread_store.add_message(thread.id, Message(role="assistant", content=assistant_content, thread_id=thread.id))
                yield f"event: done\ndata: {json.dumps({'thread_id': thread.id, 'type': 'done'})}\n\n"

        except Exception as e:
            logger.error(f"Local chat error: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            yield f"event: done\ndata: {json.dumps({'thread_id': thread.id, 'type': 'done'})}\n\n"
        finally:
            permission_requests.unsubscribe(thread.id, permission_queue)
            heartbeat_task.cancel()
            agent_task.cancel()
            permission_task.cancel()
            await asyncio.gather(agent_task, permission_task, heartbeat_task, return_exceptions=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
