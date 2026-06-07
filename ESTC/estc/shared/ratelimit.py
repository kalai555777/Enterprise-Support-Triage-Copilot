"""Per-IP token-bucket rate limiting for public-facing endpoints (pure ASGI).

Reads the per-minute allowance from ``ESTC_RATE_LIMIT_PER_MIN`` at request time;
``0`` / unset disables it (offline + CI default). The bucket is in-process, so this
is a single-replica baseline — a multi-replica deployment would back it with Redis.
``/healthz`` is always exempt so liveness probes are never throttled.

Returns HTTP 429 with a ``Retry-After`` header when the bucket is empty.
"""

from __future__ import annotations

import os
import time

_DEFAULT_EXEMPT = ("/healthz",)


def _too_many():
    async def _app(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"1"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b'{"detail":"rate limit exceeded"}'})

    return _app


class RateLimitMiddleware:
    def __init__(
        self, app, *, env_var: str = "ESTC_RATE_LIMIT_PER_MIN", exempt_paths=_DEFAULT_EXEMPT
    ) -> None:
        self.app = app
        self.env_var = env_var
        self.exempt_paths = tuple(exempt_paths)
        # ip -> (tokens, last_refill_monotonic)
        self._buckets: dict[str, tuple[float, float]] = {}

    def _client_ip(self, scope) -> str:
        headers = dict(scope.get("headers") or [])
        fwd = headers.get(b"x-forwarded-for")
        if fwd:
            return fwd.decode().split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _allow(self, ip: str, per_min: int) -> bool:
        now = time.monotonic()
        capacity = float(per_min)
        rate = per_min / 60.0  # tokens per second
        tokens, last = self._buckets.get(ip, (capacity, now))
        tokens = min(capacity, tokens + (now - last) * rate)
        if tokens < 1.0:
            self._buckets[ip] = (tokens, now)
            return False
        self._buckets[ip] = (tokens - 1.0, now)
        return True

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            per_min = int(os.getenv(self.env_var, "0"))
        except ValueError:
            per_min = 0
        path = scope.get("path", "")
        if per_min <= 0 or path in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        if not self._allow(self._client_ip(scope), per_min):
            await _too_many()(scope, receive, send)
            return
        await self.app(scope, receive, send)
