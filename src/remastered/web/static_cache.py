from __future__ import annotations

from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from remastered.web.static_assets import get_static_hashes, is_versioned_static_request

_CACHE_VERSIONED = b"public, max-age=31536000, immutable"
_CACHE_UNVERSIONED_STATIC = b"public, max-age=300"
_CACHE_HTML = b"no-cache"


def _set_header(headers: list[tuple[bytes, bytes]], name: bytes, value: bytes) -> list[tuple[bytes, bytes]]:
    filtered = [(k, v) for k, v in headers if k.lower() != name]
    filtered.append((name, value))
    return filtered


class StaticAndHtmlCacheMiddleware:
    """Long-cache versioned static assets; avoid caching HTML documents."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        query = scope.get("query_string", b"")
        hashes = get_static_hashes()
        versioned_static = is_versioned_static_request(path, query, hashes)
        is_static = path.startswith("/static/")
        is_html_route = (
            not is_static
            and path not in ("/health", "/favicon.ico")
            and not path.startswith("/media/")
        )

        async def send_wrapper(message: Message) -> None:
            if message["type"] != "http.response.start":
                await send(message)
                return
            headers = list(message.get("headers", []))
            if versioned_static:
                headers = _set_header(headers, b"cache-control", _CACHE_VERSIONED)
            elif is_static:
                headers = _set_header(headers, b"cache-control", _CACHE_UNVERSIONED_STATIC)
            elif is_html_route:
                headers = _set_header(headers, b"cache-control", _CACHE_HTML)
            await send({**message, "headers": headers})

        await self.app(scope, receive, send_wrapper)
