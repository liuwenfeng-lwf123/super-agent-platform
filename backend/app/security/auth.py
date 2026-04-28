"""
Simple token-based API authentication middleware.

Enable by setting API_SECRET_TOKEN in .env:
    API_SECRET_TOKEN=your-secret-here

Clients send the token via:
    - Header: Authorization: Bearer <token>
    - Query param: ?token=<token>

WebSocket auth uses query param: ws://host/ws/local-client?token=<token>

When API_SECRET_TOKEN is empty or unset, auth is disabled (dev mode).
"""
import logging
from fastapi import Request, WebSocket, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings

logger = logging.getLogger(__name__)

# Paths that never require auth
PUBLIC_PATHS = frozenset({"/health", "/ready", "/docs", "/openapi.json", "/redoc"})


def _extract_token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.query_params.get("token")


def verify_ws_token(websocket: WebSocket) -> bool:
    """Check WebSocket query param token. Returns True if auth passes."""
    secret = settings.api_secret_token
    if not secret:
        return True  # auth disabled
    token = websocket.query_params.get("token", "")
    return token == secret


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        secret = settings.api_secret_token
        if not secret:
            return await call_next(request)  # auth disabled

        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/ws/"):
            # WebSocket auth is handled separately in the endpoint
            return await call_next(request)

        token = _extract_token_from_request(request)
        if token != secret:
            logger.warning(f"Auth failed: {request.method} {path} from {request.client.host if request.client else '?'}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid or missing API token. Set Authorization: Bearer <token> header."},
            )

        return await call_next(request)
