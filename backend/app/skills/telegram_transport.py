import os
from typing import Any

from fastapi import HTTPException

from app.skills.channels import ChannelMessageRequest, channel_manager


class TelegramTransport:
    def _secret(self) -> str:
        return os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

    def validate_secret(self, provided_secret: str | None):
        expected_secret = self._secret()
        if expected_secret and provided_secret != expected_secret:
            raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")

    def parse_update(self, update: dict[str, Any]) -> ChannelMessageRequest | None:
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return None
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        user_id = str(sender.get("id") or "").strip()
        conversation_id = str(chat.get("id") or "").strip()
        text = str(message.get("text") or message.get("caption") or "").strip()
        if not user_id or not conversation_id or not text:
            return None
        photos = message.get("photo") if isinstance(message.get("photo"), list) else []
        photo_file_ids = [
            photo.get("file_id")
            for photo in photos
            if isinstance(photo, dict) and photo.get("file_id")
        ]
        metadata = {
            "update_id": update.get("update_id"),
            "telegram_message_id": message.get("message_id"),
            "chat_id": chat.get("id"),
            "chat_type": chat.get("type"),
            "chat_title": chat.get("title"),
            "username": sender.get("username"),
            "first_name": sender.get("first_name"),
            "last_name": sender.get("last_name"),
            "is_bot": sender.get("is_bot"),
            "photo_file_ids": photo_file_ids,
        }
        return ChannelMessageRequest(
            user_id=user_id,
            text=text,
            conversation_id=conversation_id,
            metadata=metadata,
        )

    async def handle_webhook(self, update: dict[str, Any], provided_secret: str | None) -> dict[str, Any]:
        self.validate_secret(provided_secret)
        request = self.parse_update(update)
        if request is None:
            return {
                "ok": True,
                "ignored": True,
                "reason": "unsupported_update",
            }
        result = await channel_manager.handle_message("telegram", request)
        response_text = result.get("reply") or result.get("error") or ""
        return {
            "ok": True,
            "processed": True,
            "update_id": update.get("update_id"),
            "thread_id": result.get("thread_id"),
            "session_key": result.get("session_key"),
            "configured": result.get("configured"),
            "telegram": {
                "chat_id": request.conversation_id,
                "user_id": request.user_id,
                "message_id": request.metadata.get("telegram_message_id"),
            },
            "outbound": {
                "method": "sendMessage",
                "chat_id": request.conversation_id,
                "text": response_text,
            },
            "channel_result": result,
        }


telegram_transport = TelegramTransport()
