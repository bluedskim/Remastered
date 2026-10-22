from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from remastered.web.auth_settings import AuthSettings

AUTH_COOKIE_NAME = "remastered_auth"


def issue_token(
    *,
    user_id: str,
    login_id: str,
    settings: AuthSettings | None = None,
) -> str:
    auth = settings or AuthSettings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user_id,
        "login_id": login_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=auth.jwt_expiry_seconds)).timestamp()),
    }
    return jwt.encode(payload, auth.jwt_secret, algorithm="HS256")


def decode_token(token: str, settings: AuthSettings | None = None) -> dict[str, Any] | None:
    auth = settings or AuthSettings()
    try:
        return jwt.decode(token, auth.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
