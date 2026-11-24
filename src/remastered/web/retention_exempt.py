"""Owner accounts whose job results are never removed by the retention sweep."""

from __future__ import annotations

import os
from functools import lru_cache

from remastered.web.db import JobStore
from remastered.web.identity import RequestIdentity
from remastered.web.settings import WebSettings
from remastered.web.video_download_access import (
    _normalize_email,
    trusted_account_email,
)


def parse_retention_exempt_emails(raw: str | None = None) -> frozenset[str]:
    if raw is None:
        raw = os.environ.get(
            "REMASTERED_WEB_RETENTION_EXEMPT_EMAILS",
            WebSettings().retention_exempt_emails,
        )
    parts = [p.strip() for p in (raw or "").split(",")]
    return frozenset(_normalize_email(p) for p in parts if p.strip())


@lru_cache(maxsize=1)
def retention_exempt_emails() -> frozenset[str]:
    return parse_retention_exempt_emails()


def reset_retention_exempt_emails_cache() -> None:
    retention_exempt_emails.cache_clear()


def account_email_for_user_id(store: JobStore, user_id: str) -> str | None:
    """Resolve a verified email for a job owner id (auth or legacy Google user)."""
    uid = (user_id or "").strip()
    if not uid or uid.startswith("anon-"):
        return None

    auth_row = store.get_auth_user_by_id(uid)
    if auth_row:
        email = (auth_row.get("login_id") or "").strip()
        if email and "@" in email:
            return email

    google_row = store.get_user(uid)
    if google_row:
        email = (google_row.get("email") or "").strip()
        if email and "@" in email:
            return email

    return None


def is_retention_exempt_user_id(store: JobStore, user_id: str) -> bool:
    email = account_email_for_user_id(store, user_id)
    if not email:
        return False
    return _normalize_email(email) in retention_exempt_emails()


def retention_exempt_for_identity(
    identity: RequestIdentity,
    store: JobStore,
) -> bool:
    if not identity.account_id:
        return False
    email = trusted_account_email(identity, store)
    if not email:
        return False
    return _normalize_email(email) in retention_exempt_emails()