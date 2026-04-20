from pydantic import BaseModel
from typing import Optional


class ChannelConfig(BaseModel):
    channel_type: str
    enabled: bool = False
    bot_token: Optional[str] = None
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    allowed_users: list[str] = []


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

    def list_channels(self) -> list[dict]:
        import os
        channels = []
        for key, info in self.SUPPORTED_CHANNELS.items():
            configured = all(os.getenv(e) for e in info["required_env"])
            channels.append({
                "type": key,
                "name": info["name"],
                "description": info["description"],
                "difficulty": info["difficulty"],
                "configured": configured,
                "required_env": info["required_env"],
            })
        return channels

    def get_channel_status(self, channel_type: str) -> dict:
        import os
        info = self.SUPPORTED_CHANNELS.get(channel_type)
        if not info:
            return {"error": f"Unknown channel: {channel_type}"}
        configured = all(os.getenv(e) for e in info["required_env"])
        return {
            "type": channel_type,
            "configured": configured,
            "missing_env": [e for e in info["required_env"] if not os.getenv(e)],
        }


channel_manager = ChannelManager()
