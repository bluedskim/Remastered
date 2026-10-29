from __future__ import annotations

import re
from typing import Mapping

from fastapi import Request, Response

from remastered.web.settings import WebSettings

SIGNUP_SOURCE_MAX_LEN = 64
SIGNUP_SOURCE_COOKIE = "remastered_signup_source"
SIGNUP_SOURCE_SESSION_KEY = "signup_source_first_touch"
SIGNUP_SOURCE_CONSUMED_COOKIE = "remastered_signup_ok"
_SIGNUP_SOURCE_RE = re.compile(r"^[a-z0-9_-]+$")


def normalize_signup_source(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if len(value) > SIGNUP_SOURCE_MAX_LEN:
        value = value[:SIGNUP_SOURCE_MAX_LEN]
    if not _SIGNUP_SOURCE_RE.fullmatch(value):
        return None
    return value


def signup_source_from_query_params(params: Mapping[str, str]) -> str | None:
    """Read utm_source (primary) or ref (alias). If utm_source is present, ref is ignored."""
    if "utm_source" in params:
        return normalize_signup_source(params.get("utm_source"))
    if "ref" in params:
        return normalize_signup_source(params.get("ref"))
    return None


def read_first_touch_signup_source(request: Request) -> str | None:
    cookie_val = normalize_signup_source(request.cookies.get(SIGNUP_SOURCE_COOKIE))
    if cookie_val:
        return cookie_val
    return normalize_signup_source(request.session.get(SIGNUP_SOURCE_SESSION_KEY))


def resolve_signup_source_for_new_account(
    request: Request,
    form_value: str | None,
) -> str | None:
    from_form = normalize_signup_source(form_value)
    if from_form:
        return from_form
    return read_first_touch_signup_source(request)


def capture_first_touch_signup_source(request: Request) -> str | None:
    """Persist first-touch in session when query params carry a valid source."""
    incoming = signup_source_from_query_params(request.query_params)
    if not incoming:
        return None
    if read_first_touch_signup_source(request):
        return None
    request.session[SIGNUP_SOURCE_SESSION_KEY] = incoming
    return incoming


def apply_signup_source_cookie(response: Response, source: str, *, public_base_url: str) -> None:
    secure = public_base_url.lower().startswith("https://")
    response.set_cookie(
        key=SIGNUP_SOURCE_COOKIE,
        value=source,
        max_age=400 * 24 * 60 * 60,
        path="/",
        samesite="lax",
        secure=secure,
        httponly=False,
    )


def clear_signup_source_tracking(response: Response, request: Request) -> None:
    request.session.pop(SIGNUP_SOURCE_SESSION_KEY, None)
    response.delete_cookie(SIGNUP_SOURCE_COOKIE, path="/")
    ws = WebSettings()
    secure = ws.public_base_url.lower().startswith("https://")
    response.set_cookie(
        key=SIGNUP_SOURCE_CONSUMED_COOKIE,
        value="1",
        max_age=120,
        path="/",
        samesite="lax",
        secure=secure,
        httponly=False,
    )
