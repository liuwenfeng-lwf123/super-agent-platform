from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from app.local.gateway import local_gateway, LocalClient
from app.local.agent import local_agent
from app.agents.store import thread_store
from app.models.schemas import Message
import json

local_router = APIRouter(prefix="/api/local", tags=["local"])
local_ws_router = APIRouter(tags=["local-ws"])


@local_ws_router.websocket("/ws/local-client")
async def ws_local_client(websocket: WebSocket):
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
            except Exception:
                break

            msg_type = data.get("type")

            if msg_type == "response":
                request_id = data.get("request_id")
                result = data.get("result", {})
                client.handle_response(request_id, result)

            elif msg_type == "rejection":
                request_id = data.get("request_id")
                reason = data.get("reason", "No reason provided")
                client.handle_rejection(request_id, reason)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "update_info":
                client.info.update(data.get("info", {}))

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
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


@local_router.post("/bind-thread")
async def bind_thread(thread_id: str, client_id: str):
    local_gateway.bind_thread(thread_id, client_id)
    return {"status": "bound", "thread_id": thread_id, "client_id": client_id}


@local_router.post("/unbind-thread")
async def unbind_thread(thread_id: str):
    local_gateway.unbind_thread(thread_id)
    return {"status": "unbound", "thread_id": thread_id}


@local_router.get("/audit")
async def get_audit_log(limit: int = 100):
    return local_gateway.get_audit_log(limit)


@local_router.post("/chat")
async def local_chat(request: Request):
    from fastapi.responses import StreamingResponse

    body = await request.json()
    thread_id = body.get("thread_id")
    message = body.get("message", "")
    model = body.get("model")
    mode = body.get("mode", "local")

    if thread_id:
        thread = await thread_store.get(thread_id)
        if not thread:
            thread = await thread_store.create()
    else:
        thread = await thread_store.create()

    user_msg = Message(role="user", content=message, thread_id=thread.id)
    await thread_store.add_message(thread.id, user_msg)

    async def event_generator():
        full_content = []

        async for event_str in local_agent.handle_message(
            message=message,
            thread_messages=thread.messages,
            model=model,
            thread_id=thread.id,
        ):
            event_data = json.loads(event_str)
            if event_data.get("type") == "token":
                full_content.append(event_data.get("content", ""))
                yield f"event: token\ndata: {event_str}\n\n"
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
