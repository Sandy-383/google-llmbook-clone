import re
import time
import uuid
from urllib.parse import urlsplit

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger("http")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
}


class RequestContextMiddleware:
    """Request ID, access log and security headers.

    Plain ASGI rather than BaseHTTPMiddleware so streamed (SSE) responses pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["x-request-id"] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            client = scope.get("client")
            log.info(
                "request",
                method=scope["method"],
                path=scope["path"],
                status=status_code,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
                # Already resolved from X-Forwarded-For by uvicorn's --proxy-headers.
                client=client[0] if client else None,
            )


class OriginCheckMiddleware:
    """CSRF defence for cookie auth: reject state-changing requests from foreign origins.

    Browsers always send `Origin` on cross-origin and same-origin POST/PUT/PATCH/DELETE.
    Requests without it (curl, server-to-server) carry no victim cookies, so they pass.
    Accepted: the configured origins, and the request's own origin (Origin's host equals the
    Host header), so the app works under whatever name it's served at, e.g. a tunnel URL that
    changes on every restart. A cross-site request can't make those match: the browser sets
    both headers. Session cookies are also SameSite=Lax.
    """

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed = {o.rstrip("/") for o in allowed_origins}

    def _allowed(self, origin: str, host: str | None) -> bool:
        if origin.rstrip("/") in self.allowed:
            return True
        netloc = urlsplit(origin).netloc.lower()
        return bool(netloc) and netloc == (host or "").lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in _UNSAFE_METHODS:
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None and not self._allowed(origin, headers.get("host")):
                response = JSONResponse({"detail": "Origin not allowed"}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
