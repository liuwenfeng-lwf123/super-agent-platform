"""
WeCom (企业微信) AI Bot transport — WebSocket long-connection mode.

Requires:
  pip install wecom-aibot-python-sdk
  env: WECOM_BOT_ID, WECOM_BOT_SECRET
"""
import asyncio
import json
import logging
import os
from typing import Any

from app.skills.channels import BaseTransport

logger = logging.getLogger(__name__)


class WecomTransport(BaseTransport):
    channel_type = "wecom"

    def __init__(self):
        super().__init__()
        self._bot = None
        self._stop_event = asyncio.Event()

    def is_configured(self) -> bool:
        return bool(os.getenv("WECOM_BOT_ID") and os.getenv("WECOM_BOT_SECRET"))

    async def start(self) -> None:
        """Start WeCom AI Bot WebSocket connection."""
        try:
            from wecom_aibot import AiBot
        except ImportError:
            logger.error("[wecom] wecom-aibot-python-sdk not installed. Run: pip install wecom-aibot-python-sdk")
            return

        bot_id = os.getenv("WECOM_BOT_ID", "")
        bot_secret = os.getenv("WECOM_BOT_SECRET", "")

        self._bot = AiBot(bot_id=bot_id, bot_secret=bot_secret)
        self._bot.on_message(self._on_message_sync)

        logger.info("[wecom] Starting WeCom AI Bot connection...")
        loop = asyncio.get_event_loop()
        try:
            # The SDK's run() is blocking, execute in a thread
            await loop.run_in_executor(None, self._bot.run)
        except Exception as exc:
            logger.error("[wecom] connection error: %s", exc)
            await self._stop_event.wait()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._bot:
            try:
                self._bot.stop()
            except Exception as e:
                logger.debug("Suppressed error in wecom_transport: %s", e)

    # -- WeCom message handler -----------------------------------------------

    def _on_message_sync(self, message: Any) -> str | None:
        """Synchronous callback from WeCom SDK — dispatches asynchronously."""
        try:
            user_id = str(getattr(message, "sender", "") or getattr(message, "user_id", "") or "")
            conversation_id = str(getattr(message, "conversation_id", "") or getattr(message, "chat_id", "") or "")
            msg_type = str(getattr(message, "msg_type", "text"))
            text = ""
            images = []

            if msg_type == "text":
                text = str(getattr(message, "content", "") or getattr(message, "text", "") or "")
            elif msg_type == "image":
                text = "[图片]"
                img_url = str(getattr(message, "image_url", "") or "")
                if img_url:
                    images.append(img_url)
            elif msg_type == "file":
                text = f"[文件: {getattr(message, 'file_name', '未知')}]"
            else:
                text = str(getattr(message, "content", "") or f"[{msg_type}]")

            if not text.strip() or not user_id:
                return None

            metadata = {
                "wecom_msg_id": str(getattr(message, "msg_id", "")),
                "wecom_msg_type": msg_type,
                "wecom_conversation_id": conversation_id,
            }

            loop = asyncio.get_event_loop()
            future = asyncio.run_coroutine_threadsafe(
                self._process_and_reply(user_id, text, conversation_id, images, metadata),
                loop,
            )
            # WeCom SDK expects a return string as reply
            try:
                result = future.result(timeout=120)
                return result
            except Exception as exc:
                logger.error("[wecom] dispatch error: %s", exc)
                return "处理消息时出现错误，请稍后重试。"

        except Exception as exc:
            logger.error("[wecom] message handler error: %s", exc)
            return None

    async def _process_and_reply(self, user_id: str, text: str, conversation_id: str,
                                  images: list[str], metadata: dict) -> str:
        """Process message and return reply text."""
        # Check for slash commands
        cmd, rest = self.parse_command(text)
        if cmd:
            reply = await self.handle_command(cmd, user_id, conversation_id)
            if reply:
                return reply
            text = rest if rest else text

        result = await self.dispatch(
            user_id=user_id,
            text=text,
            conversation_id=conversation_id,
            images=images,
            metadata=metadata,
        )
        return result.get("reply") or result.get("error") or "（无回复）"


wecom_transport = WecomTransport()
