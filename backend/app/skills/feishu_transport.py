"""
Feishu / Lark IM transport — long-connection (WebSocket) mode.

Requires:
  pip install lark-oapi
  env: FEISHU_APP_ID, FEISHU_APP_SECRET
"""
import asyncio
import json
import logging
import os
from typing import Any

from app.skills.channels import BaseTransport

logger = logging.getLogger(__name__)

# Feishu domain: China vs International
_FEISHU_DOMAIN_CN = "https://open.feishu.cn"
_FEISHU_DOMAIN_INTL = "https://open.larksuite.com"


class FeishuTransport(BaseTransport):
    channel_type = "feishu"

    def __init__(self):
        super().__init__()
        self._client = None
        self._ws_client = None
        self._stop_event = asyncio.Event()

    def is_configured(self) -> bool:
        return bool(os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"))

    async def start(self) -> None:
        """Start Feishu WebSocket long-connection event listener."""
        try:
            import lark_oapi as lark
            from lark_oapi.adapter.asyncio import create_async_event_handler
        except ImportError:
            logger.error("[feishu] lark-oapi not installed. Run: pip install lark-oapi")
            return

        app_id = os.getenv("FEISHU_APP_ID", "")
        app_secret = os.getenv("FEISHU_APP_SECRET", "")
        domain = os.getenv("FEISHU_DOMAIN", _FEISHU_DOMAIN_CN)

        self._client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .domain(domain) \
            .build()

        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._on_message) \
            .build()

        # WebSocket client for long-connection mode (no public IP needed)
        try:
            self._ws_client = lark.ws.Client(
                app_id=app_id,
                app_secret=app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.WARNING,
            )
            logger.info("[feishu] Starting WebSocket long-connection...")
            # ws.start() is blocking in lark-oapi, run in thread
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._ws_client.start)
        except AttributeError:
            # Fallback: if lark.ws is not available, use HTTP callback style
            logger.warning(
                "[feishu] lark-oapi WebSocket not available in this version. "
                "Use HTTP callback mode or upgrade lark-oapi."
            )
            # Keep alive until stop
            await self._stop_event.wait()
        except Exception as exc:
            logger.error("[feishu] WebSocket failed: %s", exc)
            await self._stop_event.wait()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception as e:
                logger.debug("Suppressed error in feishu_transport: %s", e)

    # -- Feishu event handler ------------------------------------------------

    def _on_message(self, ctx, event) -> None:
        """Handle im.message.receive_v1 event from Feishu."""
        try:
            msg = event.event.message
            sender = event.event.sender
            user_id = sender.sender_id.open_id if sender and sender.sender_id else ""
            chat_id = msg.chat_id if msg else ""
            msg_type = msg.message_type if msg else ""
            content_raw = msg.content if msg else ""

            # Parse message content
            text = ""
            images = []
            if msg_type == "text":
                try:
                    content = json.loads(content_raw)
                    text = content.get("text", "")
                except Exception as e:
                    logger.debug("Suppressed error in feishu_transport: %s", e)
                    text = content_raw
            elif msg_type == "image":
                text = "[图片]"
                try:
                    content = json.loads(content_raw)
                    images.append(content.get("image_key", ""))
                except Exception as e:
                    logger.debug("Suppressed error in feishu_transport: %s", e)
            else:
                text = f"[{msg_type}]"

            if not text.strip() or not user_id:
                return

            metadata = {
                "feishu_message_id": msg.message_id if msg else "",
                "feishu_chat_id": chat_id,
                "feishu_msg_type": msg_type,
                "feishu_chat_type": msg.chat_type if msg else "",
            }

            # Dispatch asynchronously
            loop = asyncio.get_event_loop()
            future = asyncio.run_coroutine_threadsafe(
                self._process_and_reply(user_id, text, chat_id, images, metadata),
                loop,
            )
            # Don't block the Feishu SDK callback thread
            future.add_done_callback(lambda f: self._log_result(f, user_id))

        except Exception as exc:
            logger.error("[feishu] message handler error: %s", exc)

    async def _process_and_reply(self, user_id: str, text: str, chat_id: str,
                                  images: list[str], metadata: dict) -> None:
        """Process message and send reply back to Feishu."""
        # Check for slash commands
        cmd, rest = self.parse_command(text)
        if cmd:
            reply = await self.handle_command(cmd, user_id, chat_id)
            if reply:
                await self._send_feishu_message(chat_id, reply)
                return
            text = rest if rest else text

        result = await self.dispatch(
            user_id=user_id,
            text=text,
            conversation_id=chat_id,
            images=images,
            metadata=metadata,
        )
        reply_text = result.get("reply") or result.get("error") or "（无回复）"
        await self._send_feishu_message(chat_id, reply_text)

    async def _send_feishu_message(self, chat_id: str, text: str) -> None:
        """Send a text message back to the Feishu chat."""
        if not self._client or not chat_id:
            return
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            content = json.dumps({"text": text}, ensure_ascii=False)
            body = CreateMessageRequestBody.builder() \
                .receive_id(chat_id) \
                .msg_type("text") \
                .content(content) \
                .build()
            request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(body) \
                .build()

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._client.im.v1.message.create(request),
            )
            if not response.success():
                logger.warning("[feishu] send failed: code=%s msg=%s", response.code, response.msg)
        except Exception as exc:
            logger.error("[feishu] send error: %s", exc)

    @staticmethod
    def _log_result(future, user_id: str):
        exc = future.exception()
        if exc:
            logger.error("[feishu] dispatch error for user %s: %s", user_id, exc)


feishu_transport = FeishuTransport()
