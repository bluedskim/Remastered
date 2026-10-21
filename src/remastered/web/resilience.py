from __future__ import annotations

import asyncio
from typing import Callable

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class MediaStreamLimitMiddleware:
    """Cap concurrent open /media responses so the server stays responsive."""

    def __init__(self, app: ASGIApp, max_streams: int) -> None:
        self.app = app
        self._sem = asyncio.Semaphore(max(1, max_streams))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/media/"):
            await self.app(scope, receive, send)
            return

        acquired = False
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=5.0)
            acquired = True
        except TimeoutError:
            response = PlainTextResponse(
                "Too many concurrent media streams. Try again shortly.",
                status_code=503,
            )
            await response(scope, receive, send)
            return

        released = False

        async def send_wrapper(message: Message) -> None:
            nonlocal released
            await send(message)
            if (
                not released
                and message["type"] == "http.response.body"
                and not message.get("more_body", False)
            ):
                self._sem.release()
                released = True

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if acquired and not released:
                self._sem.release()


def mount_health_router(app: ASGIApp) -> ASGIApp:
    """Return an ASGI app that answers /health before heavier middleware stacks."""

    async def health_only(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await app(scope, receive, send)
            return
        if scope["type"] == "http" and scope.get("path") == "/health":
            body = b'{"status":"ok"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await app(scope, receive, send)

    return health_only
