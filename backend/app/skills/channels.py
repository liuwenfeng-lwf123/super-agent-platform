import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agents.store import thread_store
from app.agents.super_agent import super_agent
from app.models.schemas import Message

logger = logging.getLogger(__name__)


class ChannelConfig(BaseModel):
    channel_type: str
    enabled: bool = False
    bot_token: Optional[str] = None
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    allowed_users: list[str] = Field(default_factory=list)


class ChannelMessageRequest(BaseModel):
    user_id: str
    text: str
    conversation_id: str = ""
    thread_id: Optional[str] = None
    model: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    mode: str = "standard"
    images: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelManager:
    SUPPORTED_CHANNELS = {
        "telegram": {
            "name": "Telegram",
            "description": "Bot API (long-polling)",
            "required_env": ["TELEGRAM_BOT_TOKEN"],
            "difficulty": "Easy",
        },
        "feishu": {
            "name": "Feishu / Lark",
            "description": "WebSocket mode",
            "required_env": ["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
            "difficulty": "Moderate",
        },
        "wecom": {
            "name": "WeCom",
            "description": "WebSocket AI Bot",
            "required_env": ["WECOM_BOT_ID", "WECOM_BOT_SECRET"],
            "difficulty": "Moderate",
        },
        "slack": {
            "name": "Slack",
            "description": "Socket Mode",
            "required_env": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
            "difficulty": "Moderate",
        },
    }

    def __init__(self):
        self._sessions: dict[str, dict[str, Any]] = {}
        self._load_sessions()

    def _sessions_path(self) -> Path:
        return Path("data") / "channel_sessions.json"

    def _load_sessions(self):
        path = self._sessions_path()
        if not path.is_file():
            self._sessions = {}
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._sessions = data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("Failed to load channel sessions: %s", exc)
            self._sessions = {}

    def _save_sessions(self):
        path = self._sessions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._sessions, ensure_ascii=False, indent=2), encoding="utf-8")

    def _is_configured(self, channel_type: str) -> bool:
        info = self.SUPPORTED_CHANNELS.get(channel_type, {})
        return all(os.getenv(env_name) for env_name in info.get("required_env", []))

    def _missing_env(self, channel_type: str) -> list[str]:
        info = self.SUPPORTED_CHANNELS.get(channel_type, {})
        return [env_name for env_name in info.get("required_env", []) if not os.getenv(env_name)]

    def _transport_info(self, channel_type: str) -> dict[str, Any]:
        info = {
            "transport": "message_api",
            "message_path": f"/api/channels/{channel_type}/messages",
        }
        if channel_type == "telegram":
            info.update({
                "transport": "webhook",
                "webhook_path": "/api/channels/telegram/webhook",
                "webhook_secret_configured": bool(os.getenv("TELEGRAM_WEBHOOK_SECRET")),
            })
        return info

    def _build_session_key(self, channel_type: str, user_id: str, conversation_id: str) -> str:
        normalized_user = user_id.strip() or "anonymous"
        normalized_conversation = conversation_id.strip()
        if normalized_conversation:
            return f"{channel_type}:{normalized_user}:{normalized_conversation}"
        return f"{channel_type}:{normalized_user}:direct"

    def list_channels(self) -> list[dict]:
        self._load_sessions()
        channels = []
        for key, info in self.SUPPORTED_CHANNELS.items():
            configured = self._is_configured(key)
            channels.append({
                "type": key,
                "name": info["name"],
                "description": info["description"],
                "difficulty": info["difficulty"],
                "configured": configured,
                "required_env": info["required_env"],
                "active_sessions": len([session for session in self._sessions.values() if session.get("channel_type") == key]),
                "message_api": True,
                **self._transport_info(key),
            })
        return channels

    def get_channel_status(self, channel_type: str) -> dict:
        self._load_sessions()
        info = self.SUPPORTED_CHANNELS.get(channel_type)
        if not info:
            return {"error": f"Unknown channel: {channel_type}"}
        return {
            "type": channel_type,
            "configured": self._is_configured(channel_type),
            "missing_env": self._missing_env(channel_type),
            "active_sessions": len([session for session in self._sessions.values() if session.get("channel_type") == channel_type]),
            "message_api": True,
            **self._transport_info(channel_type),
        }

    def list_sessions(self, channel_type: str) -> list[dict]:
        self._load_sessions()
        sessions = []
        for session_key, payload in self._sessions.items():
            if payload.get("channel_type") != channel_type:
                continue
            sessions.append({"session_key": session_key, **payload})
        sessions.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return sessions

    async def _resolve_thread(self, channel_type: str, payload: ChannelMessageRequest) -> tuple[Any, str, bool]:
        self._load_sessions()
        session_key = self._build_session_key(channel_type, payload.user_id, payload.conversation_id)
        thread = None
        created_thread = False
        if payload.thread_id:
            thread = await thread_store.get(payload.thread_id)
        if thread is None:
            existing = self._sessions.get(session_key)
            if existing:
                thread = await thread_store.get(existing.get("thread_id", ""))
        if thread is None:
            title = f"[{channel_type}] {payload.text.strip()[:50]}"
            thread = await thread_store.create(title=title)
            created_thread = True
        return thread, session_key, created_thread

    async def _run_agent(self, payload: ChannelMessageRequest, history_messages: list, thread_id: str) -> dict[str, Any]:
        full_content: list[str] = []
        usage = None
        error = None
        events: list[dict[str, Any]] = []
        async for event_str in super_agent.handle_message(
            message=payload.text,
            thread_messages=history_messages,
            model=payload.model,
            skills=payload.skills or None,
            mode=payload.mode or "standard",
            thread_id=thread_id,
            images=payload.images or None,
        ):
            try:
                event = json.loads(event_str)
            except Exception as e:
                logger.debug("Suppressed error in channels: %s", e)
                event = {"type": "raw", "content": str(event_str)}
            event_type = event.get("type")
            if event_type == "token":
                full_content.append(event.get("content", ""))
                continue
            if event_type == "done":
                usage = event.get("usage")
                continue
            if event_type == "error" and error is None:
                error = event.get("content", "")
            events.append(event)
        return {
            "reply": "".join(full_content),
            "usage": usage,
            "error": error,
            "events": events,
        }

    async def handle_message(self, channel_type: str, payload: ChannelMessageRequest) -> dict[str, Any]:
        info = self.SUPPORTED_CHANNELS.get(channel_type)
        if info is None:
            return {
                "error": f"Unknown channel: {channel_type}",
                "supported_channels": sorted(self.SUPPORTED_CHANNELS.keys()),
            }
        text = payload.text.strip()
        user_id = payload.user_id.strip()
        if not user_id:
            return {"error": "user_id is required"}
        if not text:
            return {"error": "text is required"}

        thread, session_key, created_thread = await self._resolve_thread(channel_type, payload)
        history_messages = list(thread.messages)
        channel_metadata = {
            "channel_type": channel_type,
            "channel_name": info["name"],
            "conversation_id": payload.conversation_id.strip(),
            "user_id": user_id,
            **payload.metadata,
        }
        thread.metadata.setdefault("channels", {})[session_key] = {
            "channel_type": channel_type,
            "conversation_id": payload.conversation_id.strip(),
            "user_id": user_id,
            "updated_at": datetime.now().isoformat(),
        }
        user_msg = Message(
            role="user",
            content=text,
            thread_id=thread.id,
            metadata={"channel": channel_metadata},
        )
        await thread_store.add_message(thread.id, user_msg)

        normalized_payload = payload.model_copy(update={"text": text, "user_id": user_id})
        agent_result = await self._run_agent(
            normalized_payload,
            history_messages,
            thread.id,
        )
        reply = agent_result.get("reply", "")
        if reply or agent_result.get("error"):
            assistant_msg = Message(
                role="assistant",
                content=reply or agent_result.get("error", ""),
                thread_id=thread.id,
                metadata={"channel": channel_metadata, "delivery": "channel"},
            )
            await thread_store.add_message(thread.id, assistant_msg)

        previous = self._sessions.get(session_key, {})
        self._sessions[session_key] = {
            "channel_type": channel_type,
            "thread_id": thread.id,
            "conversation_id": payload.conversation_id.strip(),
            "user_id": user_id,
            "created_at": previous.get("created_at", datetime.now().isoformat()),
            "updated_at": datetime.now().isoformat(),
            "last_message_preview": text[:120],
            "configured": self._is_configured(channel_type),
        }
        self._save_sessions()

        return {
            "channel_type": channel_type,
            "thread_id": thread.id,
            "session_key": session_key,
            "created_thread": created_thread,
            "configured": self._is_configured(channel_type),
            "reply": reply,
            "error": agent_result.get("error"),
            "usage": agent_result.get("usage"),
            "event_count": len(agent_result.get("events", [])),
            "events": agent_result.get("events", []),
        }


channel_manager = ChannelManager()


# ---------------------------------------------------------------------------
# BaseTransport — abstract base for long-running IM transports
# ---------------------------------------------------------------------------

import abc
import asyncio


class BaseTransport(abc.ABC):
    """Abstract base class for IM channel transports (Feishu, WeCom, Slack, etc.)."""

    channel_type: str = ""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    # -- abstract methods that subclasses must implement --------------------

    @abc.abstractmethod
    async def start(self) -> None:
        """Start the long-running connection (WebSocket / long-poll / Socket Mode)."""
        ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the transport."""
        ...

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Return True if all required env vars / config are present."""
        ...

    # -- helper: dispatch inbound message to ChannelManager -----------------

    async def dispatch(self, user_id: str, text: str, conversation_id: str = "",
                       images: list[str] | None = None,
                       metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Route an inbound IM message through the unified ChannelManager."""
        payload = ChannelMessageRequest(
            user_id=user_id,
            text=text,
            conversation_id=conversation_id,
            images=images or [],
            metadata=metadata or {},
        )
        return await channel_manager.handle_message(self.channel_type, payload)

    # -- handle slash commands (/new, /status, /models, /help) ---------------

    def parse_command(self, text: str) -> tuple[str | None, str]:
        """Parse a /command from message text. Returns (command, remaining_text)."""
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None, text
        parts = stripped.split(None, 1)
        cmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        return cmd, rest

    async def handle_command(self, cmd: str, user_id: str, conversation_id: str = "") -> str | None:
        """Handle built-in slash commands. Returns reply text or None if not a built-in."""
        if cmd == "/new":
            return "🆕 新会话已就绪。请发送您的消息。"
        if cmd == "/status":
            info = channel_manager.SUPPORTED_CHANNELS.get(self.channel_type, {})
            configured = channel_manager._is_configured(self.channel_type)
            sessions = channel_manager.list_sessions(self.channel_type)
            return (
                f"📊 频道状态\n"
                f"类型: {info.get('name', self.channel_type)}\n"
                f"已配置: {'✅' if configured else '❌'}\n"
                f"活跃会话: {len(sessions)}"
            )
        if cmd == "/models":
            try:
                from app.models.provider import llm_provider
                models = llm_provider.list_models()
                names = [m.get("id") or m.get("name", "?") for m in models[:10]]
                return "🤖 可用模型:\n" + "\n".join(f"  • {n}" for n in names)
            except Exception as e:
                logger.debug("Suppressed error in channels: %s", e)
                return "⚠️ 无法获取模型列表"
        if cmd == "/help":
            return (
                "📖 可用命令:\n"
                "  /new    — 开始新会话\n"
                "  /status — 查看频道状态\n"
                "  /models — 列出可用模型\n"
                "  /help   — 显示帮助\n\n"
                "直接发送消息即可与 AI 对话。"
            )
        return None

    # -- lifecycle helpers ---------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def start_background(self, loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Task:
        """Start transport as a background asyncio task."""
        async def _wrapper():
            self._running = True
            try:
                await self.start()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error("[%s] transport crashed: %s", self.channel_type, exc)
            finally:
                self._running = False

        _loop = loop or asyncio.get_event_loop()
        self._task = _loop.create_task(_wrapper())
        return self._task

    async def stop_background(self):
        """Stop the background transport task."""
        try:
            await self.stop()
        except Exception as e:
            logger.debug("Suppressed error in channels: %s", e)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._running = False


# ---------------------------------------------------------------------------
# TransportRegistry — auto-discover and start configured transports
# ---------------------------------------------------------------------------

class TransportRegistry:
    """Manages all registered IM transports."""

    def __init__(self):
        self._transports: dict[str, BaseTransport] = {}

    def register(self, transport: BaseTransport):
        self._transports[transport.channel_type] = transport

    def get(self, channel_type: str) -> BaseTransport | None:
        return self._transports.get(channel_type)

    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "channel_type": t.channel_type,
                "configured": t.is_configured(),
                "running": t.running,
            }
            for t in self._transports.values()
        ]

    async def start_configured(self):
        """Start all transports that have their env vars configured."""
        for name, transport in self._transports.items():
            if transport.is_configured() and not transport.running:
                logger.info("[IM] Starting %s transport...", name)
                transport.start_background()

    async def stop_all(self):
        """Stop all running transports."""
        for name, transport in self._transports.items():
            if transport.running:
                logger.info("[IM] Stopping %s transport...", name)
                await transport.stop_background()


transport_registry = TransportRegistry()


def _auto_register_transports():
    """Register all known transports (import is deferred to avoid circular deps)."""
    try:
        from app.skills.telegram_transport import telegram_transport
        # Wrap existing TelegramTransport as a BaseTransport-compatible adapter
        # (Telegram currently uses webhook, not long-connection, so we skip it here)
    except Exception as e:
        logger.debug("Suppressed error in channels: %s", e)

    try:
        from app.skills.feishu_transport import feishu_transport
        transport_registry.register(feishu_transport)
    except Exception as exc:
        logger.debug("feishu transport not available: %s", exc)

    try:
        from app.skills.wecom_transport import wecom_transport
        transport_registry.register(wecom_transport)
    except Exception as exc:
        logger.debug("wecom transport not available: %s", exc)

    try:
        from app.skills.slack_transport import slack_transport
        transport_registry.register(slack_transport)
    except Exception as exc:
        logger.debug("slack transport not available: %s", exc)


_auto_register_transports()
