"""Shared-secret API-key auth for the inter-service hops (defense-in-depth).

``ApiKeyMiddleware`` is a pure-ASGI gate (SSE-safe, like the request-id middleware).
It reads the expected key from the ``ESTC_API_KEY`` env var **at request time** so it
works in the decoupled classifier image (no pydantic-settings there) and so tests can
toggle it via monkeypatch. When the env var is empty/unset the gate is fully disabled
— the system stays runnable offline and in CI without secrets. ``/healthz`` and docs
endpoints are always exempt so liveness probes and schema browsing keep working.
"""

from __future__ import annotations

import os

_HEADER = b"x-api-key"
_DEFAULT_EXEMPT = ("/healthz", "/docs", "/openapi.json", "/redoc")


def _unauthorized(message: str):
    body = f'{{"detail":"{message}"}}'.encode()

    async def _app(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return _app


class ApiKeyMiddleware:
    """Reject requests whose ``X-API-Key`` does not match ``ESTC_API_KEY`` (when set)."""

    def __init__(self, app, *, env_var: str = "ESTC_API_KEY", exempt_paths=_DEFAULT_EXEMPT) -> None:
        self.app = app
        self.env_var = env_var
        self.exempt_paths = tuple(exempt_paths)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        expected = os.getenv(self.env_var, "").strip()
        path = scope.get("path", "")
        if not expected or path in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        provided = headers.get(_HEADER, b"").decode()
        if provided != expected:
            await _unauthorized("invalid or missing API key")(scope, receive, send)
            return
        await self.app(scope, receive, send)
