"""
Role-Based Access Control (RBAC) module.

Enable by setting AUTH_RBAC_ENABLED=true in .env.
Users are defined in data/users.json:
[
  {
    "username": "admin",
    "token": "admin-token-123",
    "role": "admin",
    "display_name": "Admin User"
  },
  {
    "username": "viewer",
    "token": "viewer-token-456",
    "role": "viewer",
    "display_name": "Read Only"
  }
]

Roles:
  - admin: full access
  - operator: read + write + execute tools
  - viewer: read only (GET requests only)

If RBAC is disabled but API_SECRET_TOKEN is set, falls back to simple token auth.
"""
import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

_USERS_FILE = os.path.join(settings.data_dir, "users.json")


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


# Permission matrix
_ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN: {"read", "write", "execute", "admin"},
    Role.OPERATOR: {"read", "write", "execute"},
    Role.VIEWER: {"read"},
}

# HTTP method → required permission
_METHOD_PERMISSION: dict[str, str] = {
    "GET": "read",
    "HEAD": "read",
    "OPTIONS": "read",
    "POST": "write",
    "PUT": "write",
    "PATCH": "write",
    "DELETE": "admin",
}

# Paths that require specific permissions override
_PATH_OVERRIDES: dict[str, str] = {
    "/api/chat": "execute",
    "/api/chat/send": "execute",
    "/api/local/send": "execute",
}


@dataclass
class User:
    username: str
    token: str
    role: Role
    display_name: str = ""


_users_cache: list[User] | None = None


def _load_users() -> list[User]:
    global _users_cache
    if _users_cache is not None:
        return _users_cache
    if not os.path.exists(_USERS_FILE):
        logger.debug("No users.json found at %s, RBAC will allow no users", _USERS_FILE)
        _users_cache = []
        return _users_cache
    try:
        with open(_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _users_cache = [
            User(
                username=u["username"],
                token=u["token"],
                role=Role(u.get("role", "viewer")),
                display_name=u.get("display_name", u["username"]),
            )
            for u in data
            if "username" in u and "token" in u
        ]
        logger.info("Loaded %d RBAC users from %s", len(_users_cache), _USERS_FILE)
    except Exception as e:
        logger.error("Failed to load users.json: %s", e)
        _users_cache = []
    return _users_cache


def reload_users() -> None:
    """Force reload users from disk (useful after editing users.json)."""
    global _users_cache
    _users_cache = None
    _load_users()


def authenticate_token(token: str) -> User | None:
    """Find user by token. Returns User or None."""
    for user in _load_users():
        if user.token == token:
            return user
    return None


def check_permission(user: User, permission: str) -> bool:
    """Check if user's role grants the given permission."""
    role_perms = _ROLE_PERMISSIONS.get(user.role, set())
    return permission in role_perms


def get_required_permission(method: str, path: str) -> str:
    """Determine required permission for a request."""
    # Check path overrides first
    for prefix, perm in _PATH_OVERRIDES.items():
        if path.startswith(prefix):
            return perm
    return _METHOD_PERMISSION.get(method.upper(), "read")


# Paths that never require RBAC (public or handled elsewhere)
_PUBLIC_PATHS = frozenset({"/health", "/ready", "/docs", "/openapi.json", "/redoc"})


class RBACMiddleware(BaseHTTPMiddleware):
    """RBAC middleware — only active when AUTH_RBAC_ENABLED=true."""

    async def dispatch(self, request: Request, call_next):
        if not settings.auth_rbac_enabled:
            return await call_next(request)

        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/ws/"):
            return await call_next(request)

        # Extract token
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else request.query_params.get("token", "")

        if not token:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing authentication token."},
            )

        user = authenticate_token(token)
        if not user:
            logger.warning("RBAC: unknown token from %s %s", request.method, path)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid token."},
            )

        # Check permission
        required = get_required_permission(request.method, path)
        if not check_permission(user, required):
            logger.warning(
                "RBAC: user %s (role=%s) denied %s for %s %s",
                user.username, user.role.value, required, request.method, path,
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": f"Permission denied. Required: {required}, your role: {user.role.value}"},
            )

        # Attach user info to request state for downstream use
        request.state.user = user
        return await call_next(request)
