import asyncio
import json
import time
import uuid
import logging
from typing import Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class LocalClient:
    def __init__(self, client_id: str, websocket: WebSocket, info: dict = None):
        self.client_id = client_id
        self.websocket = websocket
        self.info = info or {}
        self.connected_at = time.time()
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._auto_approve = False

    async def send_request(self, action: str, params: dict, timeout: float = 120) -> dict:
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_requests[request_id] = future

        message = {
            "type": "request",
            "request_id": request_id,
            "action": action,
            "params": params,
        }

        try:
            await self.websocket.send_json(message)
            logger.info(f"Sent request {request_id} to client {self.client_id}: action={action}")
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            logger.error(f"Failed to send request to client {self.client_id}: {e}")
            return {"success": False, "error": f"Send failed: {e}"}

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.info(f"Got response for {request_id}: success={result.get('success')}")
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            return {"success": False, "error": f"Timeout after {timeout}s"}
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            return {"success": False, "error": str(e)}

    def handle_response(self, request_id: str, result: dict):
        future = self._pending_requests.pop(request_id, None)
        if future and not future.done():
            future.set_result(result)

    def handle_rejection(self, request_id: str, reason: str):
        future = self._pending_requests.pop(request_id, None)
        if future and not future.done():
            future.set_result({"success": False, "error": f"User rejected: {reason}"})


class LocalGateway:
    def __init__(self):
        self._clients: dict[str, LocalClient] = {}
        self._thread_client_map: dict[str, str] = {}
        self._audit_log: list[dict] = []

    def register_client(self, client: LocalClient):
        self._clients[client.client_id] = client

    def unregister_client(self, client_id: str):
        self._clients.pop(client_id, None)
        threads_to_remove = [t for t, c in self._thread_client_map.items() if c == client_id]
        for t in threads_to_remove:
            del self._thread_client_map[t]

    def get_client(self, client_id: str) -> Optional[LocalClient]:
        return self._clients.get(client_id)

    def bind_thread(self, thread_id: str, client_id: str):
        self._thread_client_map[thread_id] = client_id

    def unbind_thread(self, thread_id: str):
        self._thread_client_map.pop(thread_id, None)

    def get_client_for_thread(self, thread_id: str) -> Optional[LocalClient]:
        client_id = self._thread_client_map.get(thread_id)
        if client_id:
            return self._clients.get(client_id)
        if self._clients:
            return next(iter(self._clients.values()))
        return None

    def list_clients(self) -> list[dict]:
        return [
            {
                "client_id": c.client_id,
                "info": c.info,
                "connected_at": c.connected_at,
                "auto_approve": c._auto_approve,
            }
            for c in self._clients.values()
        ]

    def set_auto_approve(self, client_id: str, enabled: bool):
        client = self._clients.get(client_id)
        if client:
            client._auto_approve = enabled

    def add_audit(self, client_id: str, action: str, params: dict, result: dict):
        self._audit_log.append({
            "timestamp": time.time(),
            "client_id": client_id,
            "action": action,
            "params_summary": {k: str(v)[:100] for k, v in params.items()},
            "success": result.get("success", False),
        })
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-500:]

    def get_audit_log(self, limit: int = 100) -> list[dict]:
        return self._audit_log[-limit:]


local_gateway = LocalGateway()
