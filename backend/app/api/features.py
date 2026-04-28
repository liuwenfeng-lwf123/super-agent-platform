"""API endpoints for feature status and configuration."""
import os
import platform
import shutil
from fastapi import APIRouter
from pydantic import BaseModel
from app.config import settings
from app.api.token_budget import _write_env_key

router = APIRouter(prefix="/api", tags=["Features"])

# Map feature id -> env key + settings attr
_TOGGLEABLE = {
    "sqlite_store": ("THREAD_STORE_BACKEND", "thread_store_backend", "sqlite", "json"),
    "rbac": ("AUTH_RBAC_ENABLED", "auth_rbac_enabled", "true", "false"),
    "sandbox": ("SANDBOX_ENABLED", "sandbox_enabled", "true", "false"),
}


class ToggleRequest(BaseModel):
    feature_id: str
    enable: bool


@router.get("/features")
async def get_features():
    """Return status of all new platform features."""
    # SQLite store
    sqlite_active = getattr(settings, "thread_store_backend", "json") == "sqlite"

    # RBAC
    rbac_enabled = getattr(settings, "auth_rbac_enabled", False)
    has_token = bool(getattr(settings, "api_secret_token", None))

    # Sandbox
    sandbox_enabled = getattr(settings, "sandbox_enabled", False)
    os_name = platform.system()
    if os_name == "Darwin":
        sandbox_type = "macOS sandbox-exec"
    elif os_name == "Linux":
        bwrap = shutil.which("bwrap")
        sandbox_type = "Linux bubblewrap" if bwrap else "Linux (bwrap not installed)"
    else:
        sandbox_type = "Not supported"

    # Streaming diff
    streaming_diff = True  # Always available now

    # Direct mode
    direct_mode = True  # Always available

    # Intent classification
    intent_classify = True

    # VS Code extension
    vscode_ext = True  # Scaffold exists

    return {
        "features": [
            {
                "id": "streaming_diff",
                "name": "实时代码 Diff",
                "description": "Agent 写文件时自动计算并展示代码差异，实时显示在聊天界面中",
                "status": "active",
                "category": "frontend",
                "how_to_use": "让 Agent 创建或修改文件即可自动触发，例如发送「写一个 hello.py」",
                "toggleable": False,
            },
            {
                "id": "sqlite_store",
                "name": "SQLite 存储",
                "description": "用 SQLite 数据库替代 JSON 文件存储会话，更快更可靠",
                "status": "active" if sqlite_active else "available",
                "category": "storage",
                "how_to_use": "点击开启后重启后端生效",
                "toggleable": True,
                "config": {"current": "sqlite" if sqlite_active else "json"},
            },
            {
                "id": "rbac",
                "name": "角色权限控制 (RBAC)",
                "description": "多用户角色权限管理，控制谁能访问哪些 API",
                "status": "active" if rbac_enabled else "available",
                "category": "security",
                "how_to_use": "点击开启后重启后端生效",
                "toggleable": True,
                "config": {"enabled": rbac_enabled, "has_token": has_token},
            },
            {
                "id": "sandbox",
                "name": f"代码沙箱 ({sandbox_type})",
                "description": "安全执行用户代码，支持 macOS sandbox-exec 和 Linux bubblewrap",
                "status": "active" if sandbox_enabled else "available",
                "category": "security",
                "how_to_use": "点击开启后重启后端生效",
                "toggleable": True,
                "config": {"enabled": sandbox_enabled, "os": os_name, "type": sandbox_type},
            },
            {
                "id": "direct_mode",
                "name": "本地直连模式",
                "description": "无需启动服务器，在终端直接运行 Agent，适合脚本和自动化场景",
                "status": "active" if direct_mode else "unavailable",
                "category": "cli",
                "how_to_use": "终端运行: python -m app.local.direct \"你的问题\"",
                "toggleable": False,
            },
            {
                "id": "intent_classify",
                "name": "LLM 意图分类",
                "description": "用大模型对模糊用户意图做二次分类，提高理解准确度",
                "status": "active" if intent_classify else "unavailable",
                "category": "ai",
                "how_to_use": "自动生效，对搜索/截图等意图识别更准确",
                "toggleable": False,
            },
            {
                "id": "vscode_extension",
                "name": "VS Code 插件",
                "description": "在 VS Code 中右键发送代码给 Agent，侧边栏聊天，无需切换窗口",
                "status": "available",
                "category": "integration",
                "how_to_use": "cd vscode-extension && npm install && 按 F5 启动开发模式",
                "toggleable": False,
            },
            {
                "id": "exception_cleanup",
                "name": "异常日志完善",
                "description": "后端 145 处静默异常全部加了日志记录，出问题不再石沉大海",
                "status": "active",
                "category": "reliability",
                "how_to_use": "自动生效，查看后端日志可看到之前被隐藏的错误",
                "toggleable": False,
            },
        ]
    }


@router.post("/features/toggle")
async def toggle_feature(req: ToggleRequest):
    """Toggle a feature on/off. Writes to .env and updates runtime settings."""
    spec = _TOGGLEABLE.get(req.feature_id)
    if not spec:
        return {"success": False, "error": f"Feature '{req.feature_id}' is not toggleable"}

    env_key, attr_name, on_value, off_value = spec
    new_value = on_value if req.enable else off_value

    # Write to .env
    _write_env_key(env_key, new_value)

    # Update runtime settings immediately
    if attr_name == "thread_store_backend":
        settings.thread_store_backend = new_value
    elif attr_name == "auth_rbac_enabled":
        settings.auth_rbac_enabled = req.enable
    elif attr_name == "sandbox_enabled":
        settings.sandbox_enabled = req.enable

    action = "已开启" if req.enable else "已关闭"
    need_restart = req.feature_id == "sqlite_store"  # SQLite needs restart to swap store
    return {
        "success": True,
        "message": f"{action}。" + ("存储切换需要重启后端才能完全生效。" if need_restart else "已即时生效。"),
        "feature_id": req.feature_id,
        "enabled": req.enable,
    }
