"""Shared structured logging + request-ID propagation for the FastAPI services.

``configure_logging(service)`` installs a single formatter that stamps every log
line with the service name and the current request id. ``RequestIdMiddleware`` is a
pure-ASGI middleware (deliberately NOT ``BaseHTTPMiddleware``, which buffers the
response body and would break the orchestrator's SSE stream): it reads or mints an
``X-Request-ID``, binds it to a context var for the duration of the request, and
echoes it back on the response so a request can be traced end-to-end across services.
"""

from __future__ import annotations

import contextvars
import logging
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_HEADER = "x-request-id"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(service: str, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            f"%(asctime)s %(levelname)s [{service}] [req=%(request_id)s] %(name)s: %(message)s"
        )
    )
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


class RequestIdMiddleware:
    """Pure-ASGI middleware: bind a request id and echo it on the response header."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw = headers.get(_HEADER.encode())
        rid = raw.decode() if raw else uuid.uuid4().hex
        token = request_id_var.set(rid)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message_headers = message.setdefault("headers", [])
                message_headers.append((_HEADER.encode(), rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)
