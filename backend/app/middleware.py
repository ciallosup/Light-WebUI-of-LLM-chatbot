import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from fastapi import Request
from starlette.responses import JSONResponse, Response

from backend.app.config import Settings

logger = logging.getLogger("app.middleware")


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_sec: int = 60):
        self.max_requests = max(1, int(max_requests))
        self.window_sec = max(1, int(window_sec))
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        bucket = self._buckets[key]
        cutoff = now - self.window_sec
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self.max_requests:
            return False

        bucket.append(now)
        return True


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def attach_common_response_headers(response: Response, request_id: str, settings: Settings) -> None:
    response.headers["X-Request-ID"] = request_id
    if settings.secure_headers_enabled:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"


def build_rate_limit_middleware(
    limiter: InMemoryRateLimiter,
    settings: Settings,
) -> Callable[[Request, Callable[..., Any]], Any]:
    async def _middleware(request: Request, call_next: Callable[..., Any]):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id

        start = time.perf_counter()
        client_host = request.client.host if request.client else "unknown"
        key = f"{client_host}:{request.url.path}"

        if request.url.path.startswith("/api/") and not limiter.allow(key):
            logger.warning(
                "rate_limited request_id=%s ip=%s path=%s",
                request_id,
                client_host,
                request.url.path,
            )
            response = JSONResponse(status_code=429, content={"detail": "Too Many Requests"})
            attach_common_response_headers(response, request_id, settings)
            return response

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.exception(
                "request_failed request_id=%s method=%s path=%s ip=%s latency_ms=%s",
                request_id,
                request.method,
                request.url.path,
                client_host,
                elapsed_ms,
            )
            raise

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "request_done request_id=%s method=%s path=%s ip=%s status=%s latency_ms=%s",
            request_id,
            request.method,
            request.url.path,
            client_host,
            response.status_code,
            elapsed_ms,
        )
        attach_common_response_headers(response, request_id, settings)
        return response

    return _middleware