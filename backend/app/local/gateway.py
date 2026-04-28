import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional
from fastapi import WebSocket

from app.config import settings

logger = logging.getLogger(__name__)
LOCAL_AUDIT_PATH = Path(settings.data_dir) / "local_audit.jsonl"
LOCAL_PERMISSIONS_PATH = Path(settings.data_dir) / "local_permissions.json"


class LocalClient:
    def __init__(self, client_id: str, websocket: WebSocket, info: dict = None):
        self.client_id = client_id
        self.websocket = websocket
        self.info = info or {}
        self.connected_at = time.time()
        self.last_seen = time.time()
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._stream_callbacks: dict[str, Any] = {}  # request_id -> callback
        self._auto_approve = False
        self._tool_auto_approve: set[str] = set()  # per-tool always-allow

    def touch(self):
        self.last_seen = time.time()

    async def send_request(self, action: str, params: dict, timeout: float = 120, force_auto_approve: bool = False) -> dict:
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_requests[request_id] = future

        message = {
            "type": "request",
            "request_id": request_id,
            "action": action,
            "params": params,
            "auto_approve": self._auto_approve or force_auto_approve,
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

    async def send_request_streaming(self, action: str, params: dict, on_chunk, timeout: float = 120, force_auto_approve: bool = False) -> dict:
        """Like send_request but calls on_chunk(stream_name, data) for each streamed line."""
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_requests[request_id] = future
        self._stream_callbacks[request_id] = on_chunk

        message = {
            "type": "request",
            "request_id": request_id,
            "action": action,
            "params": params,
            "auto_approve": self._auto_approve or force_auto_approve,
        }

        try:
            await self.websocket.send_json(message)
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            self._stream_callbacks.pop(request_id, None)
            return {"success": False, "error": f"Send failed: {e}"}

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            return {"success": False, "error": f"Timeout after {timeout}s"}
        finally:
            self._stream_callbacks.pop(request_id, None)
            self._pending_requests.pop(request_id, None)

    def handle_stream_output(self, request_id: str, stream: str, data: str):
        cb = self._stream_callbacks.get(request_id)
        if cb:
            try:
                cb(stream, data)
            except Exception as e:
                logger.debug("Suppressed error in gateway: %s", e)

    def handle_response(self, request_id: str, result: dict):
        future = self._pending_requests.pop(request_id, None)
        self._stream_callbacks.pop(request_id, None)
        if future and not future.done():
            future.set_result(result)

    def handle_rejection(self, request_id: str, reason: str):
        future = self._pending_requests.pop(request_id, None)
        self._stream_callbacks.pop(request_id, None)
        if future and not future.done():
            future.set_result({"success": False, "error": f"User rejected: {reason}"})

    def disconnect(self):
        """Cancel all pending futures when client disconnects."""
        for request_id, future in list(self._pending_requests.items()):
            if not future.done():
                future.set_result({"success": False, "error": "Client disconnected"})
        self._pending_requests.clear()


class LocalGateway:
    def __init__(self):
        self._clients: dict[str, LocalClient] = {}
        self._thread_client_map: dict[str, str] = {}
        self._audit_log: list[dict] = []
        self._heartbeat_task: asyncio.Task | None = None
        self._global_auto_approve = False
        self._tool_auto_approve: set[str] = set()
        self._load_permission_settings()

    def _load_permission_settings(self):
        if not LOCAL_PERMISSIONS_PATH.exists():
            return
        try:
            data = json.loads(LOCAL_PERMISSIONS_PATH.read_text(encoding="utf-8"))
            self._global_auto_approve = bool(data.get("auto_approve", False))
            self._tool_auto_approve = set(data.get("tool_auto_approve", []))
        except Exception as e:
            logger.debug("Suppressed error in gateway: %s", e)

    def _save_permission_settings(self):
        try:
            LOCAL_PERMISSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOCAL_PERMISSIONS_PATH.write_text(
                json.dumps({
                    "auto_approve": self._global_auto_approve,
                    "tool_auto_approve": sorted(self._tool_auto_approve),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("Suppressed error in gateway: %s", e)

    def start_heartbeat(self):
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(30)
            now = time.time()
            stale = [cid for cid, c in self._clients.items() if now - c.last_seen > 90]
            for cid in stale:
                logger.warning(f"Client {cid} heartbeat timeout ({int(now - self._clients[cid].last_seen)}s), removing")
                self.unregister_client(cid)

    def register_client(self, client: LocalClient):
        client._auto_approve = self._global_auto_approve
        client._tool_auto_approve = set(self._tool_auto_approve)
        self._clients[client.client_id] = client

    def unregister_client(self, client_id: str):
        client = self._clients.pop(client_id, None)
        if client:
            client.disconnect()
        threads_to_remove = [t for t, c in self._thread_client_map.items() if c == client_id]
        for t in threads_to_remove:
            del self._thread_client_map[t]
        logger.info(f"Unregistered client {client_id}, cleared {len(threads_to_remove)} thread bindings")

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
                "tool_auto_approve": sorted(c._tool_auto_approve),
            }
            for c in self._clients.values()
        ]

    def set_auto_approve(self, client_id: str, enabled: bool):
        self._global_auto_approve = enabled
        for client in self._clients.values():
            client._auto_approve = enabled
        self._save_permission_settings()

    def set_tool_auto_approve(self, client_id: str, tool: str, enabled: bool):
        if not tool:
            return
        if enabled:
            self._tool_auto_approve.add(tool)
        else:
            self._tool_auto_approve.discard(tool)
        for client in self._clients.values():
            client._tool_auto_approve = set(self._tool_auto_approve)
        self._save_permission_settings()

    def is_tool_auto_approved(self, client_id: str, tool: str) -> bool:
        client = self._clients.get(client_id)
        if not client:
            return self._global_auto_approve or tool in self._tool_auto_approve
        return client._auto_approve or tool in client._tool_auto_approve

    def add_audit(self, client_id: str, action: str, params: dict, result: dict, thread_id: str = ""):
        entry = {
            "timestamp": time.time(),
            "client_id": client_id,
            "thread_id": thread_id,
            "action": action,
            "params_summary": {k: str(v)[:100] for k, v in params.items()},
            "success": result.get("success", False),
        }
        self._audit_log.append(entry)
        try:
            LOCAL_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOCAL_AUDIT_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug("Suppressed error in gateway: %s", e)
        try:
            from app.agents.tool_runtime import record_tool_event
            record_tool_event(
                tool=f"local_{action}",
                category="local",
                thread_id=thread_id,
                mode="local",
                input_preview=json.dumps(params, ensure_ascii=False)[:600],
                output_preview=json.dumps(result, ensure_ascii=False)[:1000],
                success=bool(result.get("success", False)),
                source="local",
                client_id=client_id,
            )
        except Exception as e:
            logger.debug("Suppressed error in gateway: %s", e)
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-500:]

    def get_audit_log(self, limit: int = 100, thread_id: str = "") -> list[dict]:
        entries = []
        seen: set[str] = set()
        for entry in self._load_persisted_audit() + self._audit_log:
            key = "|".join([
                str(entry.get("timestamp", "")),
                str(entry.get("client_id", "")),
                str(entry.get("thread_id", "")),
                str(entry.get("action", "")),
            ])
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
        if thread_id:
            entries = [entry for entry in entries if entry.get("thread_id") == thread_id]
        return entries[-limit:]

    def _load_persisted_audit(self, limit: int = 1000) -> list[dict]:
        if not LOCAL_AUDIT_PATH.exists():
            return []
        try:
            lines = LOCAL_AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            logger.debug("Suppressed error in gateway: %s", e)
            return []
        entries = []
        for line in lines[-limit:]:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    entries.append(item)
            except Exception as e:
                logger.debug("Suppressed error in gateway: %s", e)
        return entries


local_gateway = LocalGateway()
