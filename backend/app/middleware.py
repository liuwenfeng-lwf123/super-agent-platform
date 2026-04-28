"""
Performance middleware: request timing, slow request warnings, and rate limiting.
"""
import logging
import time
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

SLOW_REQUEST_THRESHOLD = 5.0  # seconds


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Log request duration and warn on slow requests."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response: Response = await call_next(request)
        elapsed = time.time() - start

        path = request.url.path
        method = request.method
        status = response.status_code

        # Add timing header
        response.headers["X-Response-Time"] = f"{elapsed:.3f}s"

        if elapsed > SLOW_REQUEST_THRESHOLD:
            logger.warning(
                "SLOW REQUEST: %s %s took %.2fs (status=%d)",
                method, path, elapsed, status,
            )
        elif elapsed > 1.0:
            logger.info(
                "%s %s %.2fs (status=%d)",
                method, path, elapsed, status,
            )

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter for chat endpoints."""

    def __init__(self, app, max_requests: int = 10, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def _get_key(self, request: Request) -> str:
        """Rate limit key: thread_id from body or client IP."""
        # For SSE chat endpoints, use thread_id if available
        forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
        return client_ip

    def _is_rate_limited(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        # Clean old entries
        self._buckets[key] = [t for t in self._buckets[key] if t > window_start]
        if len(self._buckets[key]) >= self.max_requests:
            return True
        self._buckets[key].append(now)
        return False

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Only rate-limit chat endpoints
        if "/chat" in path and request.method == "POST":
            key = self._get_key(request)
            if self._is_rate_limited(key):
                remaining_wait = self.window_seconds - (time.time() - self._buckets[key][0])
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "Rate limited",
                        "message": f"Too many requests. Please wait {remaining_wait:.0f}s.",
                        "retry_after": int(remaining_wait),
                    },
                    headers={"Retry-After": str(int(remaining_wait))},
                )

        return await call_next(request)
