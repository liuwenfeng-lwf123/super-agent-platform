"""
Slack IM transport — Socket Mode (no public URL needed).

Requires:
  pip install slack-bolt
  env: SLACK_BOT_TOKEN (xoxb-...), SLACK_APP_TOKEN (xapp-...)
"""
import asyncio
import logging
import os
from typing import Any

from app.skills.channels import BaseTransport

logger = logging.getLogger(__name__)


class SlackTransport(BaseTransport):
    channel_type = "slack"

    def __init__(self):
        super().__init__()
        self._app = None
        self._handler = None
        self._stop_event = asyncio.Event()

    def is_configured(self) -> bool:
        return bool(os.getenv("SLACK_BOT_TOKEN") and os.getenv("SLACK_APP_TOKEN"))

    async def start(self) -> None:
        """Start Slack Socket Mode handler."""
        try:
            from slack_bolt.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError:
            logger.error("[slack] slack-bolt not installed. Run: pip install slack-bolt")
            return

        bot_token = os.getenv("SLACK_BOT_TOKEN", "")
        app_token = os.getenv("SLACK_APP_TOKEN", "")
        allowed_users_raw = os.getenv("SLACK_ALLOWED_USERS", "")
        allowed_users = [u.strip() for u in allowed_users_raw.split(",") if u.strip()] if allowed_users_raw else []

        self._app = AsyncApp(token=bot_token)

        # Register event handlers
        @self._app.event("message")
        async def handle_message(event: dict, say):
            await self._on_message(event, say, allowed_users)

        @self._app.event("app_mention")
        async def handle_mention(event: dict, say):
            await self._on_message(event, say, allowed_users)

        self._handler = AsyncSocketModeHandler(self._app, app_token)
        logger.info("[slack] Starting Socket Mode handler...")
        try:
            await self._handler.start_async()
        except Exception as exc:
            logger.error("[slack] Socket Mode error: %s", exc)
            await self._stop_event.wait()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._handler:
            try:
                await self._handler.close_async()
            except Exception as e:
                logger.debug("Suppressed error in slack_transport: %s", e)

    # -- Slack event handler -------------------------------------------------

    async def _on_message(self, event: dict, say, allowed_users: list[str]) -> None:
        """Handle incoming Slack message or app_mention event."""
        try:
            # Ignore bot messages to prevent loops
            if event.get("bot_id") or event.get("subtype") == "bot_message":
                return

            user_id = event.get("user", "")
            channel_id = event.get("channel", "")
            text = event.get("text", "").strip()
            thread_ts = event.get("thread_ts") or event.get("ts", "")

            if not text or not user_id:
                return

            # Check allowed users
            if allowed_users and user_id not in allowed_users:
                logger.debug("[slack] ignoring message from non-allowed user: %s", user_id)
                return

            # Strip bot mention from text (e.g., <@U12345> hello → hello)
            import re
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
            if not text:
                return

            metadata = {
                "slack_channel_id": channel_id,
                "slack_thread_ts": thread_ts,
                "slack_event_ts": event.get("ts", ""),
                "slack_event_type": event.get("type", ""),
            }

            # Extract image URLs from files
            images = []
            for f in event.get("files", []):
                if isinstance(f, dict) and f.get("mimetype", "").startswith("image/"):
                    images.append(f.get("url_private", ""))

            # Check for slash commands
            cmd, rest = self.parse_command(text)
            if cmd:
                reply = await self.handle_command(cmd, user_id, channel_id)
                if reply:
                    await say(text=reply, thread_ts=thread_ts)
                    return
                text = rest if rest else text

            result = await self.dispatch(
                user_id=user_id,
                text=text,
                conversation_id=channel_id,
                images=images,
                metadata=metadata,
            )

            reply_text = result.get("reply") or result.get("error") or "（无回复）"

            # Split long messages (Slack has 4000 char limit per message)
            MAX_LEN = 3900
            if len(reply_text) <= MAX_LEN:
                await say(text=reply_text, thread_ts=thread_ts)
            else:
                chunks = [reply_text[i:i + MAX_LEN] for i in range(0, len(reply_text), MAX_LEN)]
                for chunk in chunks:
                    await say(text=chunk, thread_ts=thread_ts)

        except Exception as exc:
            logger.error("[slack] message handler error: %s", exc)
            try:
                await say(text=f"⚠️ 处理消息时出现错误: {exc}", thread_ts=event.get("thread_ts") or event.get("ts", ""))
            except Exception as e:
                logger.debug("Suppressed error in slack_transport: %s", e)


slack_transport = SlackTransport()
