from __future__ import annotations

from uuid import uuid4

from fastapi import Request

OWNER_SESSION_KEY = "owner_id"


def client_ip(request: Request) -> str:
    """Client IP for abuse limits (Cloudflare → forwarded chain → direct)."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def get_or_create_owner_id(request: Request) -> str:
    owner_id = request.session.get(OWNER_SESSION_KEY)
    if not owner_id:
        owner_id = f"anon-{uuid4().hex}"
        request.session[OWNER_SESSION_KEY] = owner_id
    return str(owner_id)


def session_middleware_kwargs(public_base_url: str, session_secret: str) -> dict:
    https = public_base_url.lower().startswith("https://")
    return {
        "secret_key": session_secret,
        "session_cookie": "remastered_session",
        "max_age": 365 * 24 * 60 * 60,
        "same_site": "lax",
        "https_only": https,
    }
