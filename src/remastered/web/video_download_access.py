"""Feature gate for on-demand remastered video downloads."""

from __future__ import annotations

import os
from functools import lru_cache

from remastered.web.db import JobStore
from remastered.web.identity import RequestIdentity
from remastered.web.settings import WebSettings


def _normalize_email(email: str) -> str:
    return (email or "").strip().casefold()


def parse_video_download_allowed_emails(raw: str | None = None) -> frozenset[str]:
    if raw is None:
        raw = os.environ.get(
            "REMASTERED_WEB_VIDEO_DOWNLOAD_ALLOWED_EMAILS",
            WebSettings().video_download_allowed_emails,
        )
    parts = [p.strip() for p in (raw or "").split(",")]
    return frozenset(_normalize_email(p) for p in parts if p.strip())


@lru_cache(maxsize=1)
def video_download_allowed_emails() -> frozenset[str]:
    return parse_video_download_allowed_emails()


def reset_video_download_allowed_emails_cache() -> None:
    video_download_allowed_emails.cache_clear()


def trusted_account_email(
    identity: RequestIdentity,
    store: JobStore | None = None,
) -> str | None:
    """Return a verified email for the signed-in account, if any."""
    if not identity.account_id:
        return None

    login_id = (identity.login_id or "").strip()
    if login_id and "@" in login_id:
        # Email/password JWT carries auth_users.login_id; signup requires email verification.
        return login_id

    if store is None:
        return None

    auth_row = store.get_auth_user_by_id(identity.account_id)
    if auth_row:
        email = (auth_row.get("login_id") or "").strip()
        if email and "@" in email:
            return email

    # Legacy YouTube OAuth rows in ``users`` (Google-verified email at token exchange).
    google_row = store.get_user(identity.account_id)
    if google_row:
        email = (google_row.get("email") or "").strip()
        if email and "@" in email:
            return email

    return None


def video_download_allowed_for_identity(
    identity: RequestIdentity,
    store: JobStore | None = None,
) -> bool:
    email = trusted_account_email(identity, store)
    if not email:
        return False
    return _normalize_email(email) in video_download_allowed_emails()
