from __future__ import annotations

from urllib.parse import quote

from fastapi import Request


def safe_next_path(raw: str | None) -> str | None:
    """Return a same-site relative path for post-auth redirect, or None if unsafe."""
    if raw is None:
        return None
    path = raw.strip()
    if not path.startswith("/"):
        return None
    if path.startswith("//"):
        return None
    if "://" in path:
        return None
    if "\\" in path:
        return None
    if "\0" in path:
        return None
    return path


def post_auth_redirect_target(next_raw: str | None, default: str = "/") -> str:
    safe = safe_next_path(next_raw)
    return safe if safe is not None else default


def signup_url(next_path: str | None = None) -> str:
    safe = safe_next_path(next_path)
    if safe:
        return f"/signup?next={quote(safe, safe='')}"
    return "/signup"


def login_url(next_path: str | None = None) -> str:
    safe = safe_next_path(next_path)
    if safe:
        return f"/login?next={quote(safe, safe='')}"
    return "/login"


def request_prefers_auth_redirect(request: Request) -> bool:
    """True for normal browser navigation; False for XHR/fetch-style requests."""
    if request.headers.get("HX-Request"):
        return False
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return False
    sec_fetch_dest = (request.headers.get("sec-fetch-dest") or "").lower()
    if sec_fetch_dest == "empty":
        return False
    sec_fetch_mode = (request.headers.get("sec-fetch-mode") or "").lower()
    if sec_fetch_mode in ("cors", "no-cors"):
        return False
    return True
