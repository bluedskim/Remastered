from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from fastapi import Request

from remastered.web.auth_jwt import AUTH_COOKIE_NAME, decode_token
from remastered.web.auth_settings import AuthSettings

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


@dataclass(frozen=True)
class RequestIdentity:
    anon_id: str
    account_id: str | None
    login_id: str | None

    @property
    def job_owner_id(self) -> str:
        return self.account_id or self.anon_id

    def job_access_allowed(self, job_user_id: str) -> bool:
        if job_user_id == self.anon_id:
            return True
        if self.account_id and job_user_id == self.account_id:
            return True
        return False


def get_account_from_request(request: Request) -> tuple[str | None, str | None]:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None, None
    payload = decode_token(token, AuthSettings())
    if not payload:
        return None, None
    user_id = payload.get("sub")
    if not user_id:
        return None, None
    login_id = payload.get("login_id")
    if not login_id:
        login_id = payload.get("nickname")
    return str(user_id), str(login_id) if login_id else None


def resolve_identity(request: Request) -> RequestIdentity:
    anon_id = get_or_create_owner_id(request)
    account_id, login_id = get_account_from_request(request)
    return RequestIdentity(
        anon_id=anon_id,
        account_id=account_id,
        login_id=login_id,
    )


def session_middleware_kwargs(public_base_url: str, session_secret: str) -> dict:
    https = public_base_url.lower().startswith("https://")
    return {
        "secret_key": session_secret,
        "session_cookie": "remastered_session",
        "max_age": 365 * 24 * 60 * 60,
        "same_site": "lax",
        "https_only": https,
    }
