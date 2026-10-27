from __future__ import annotations

from uuid import uuid4

from fastapi import Request

from remastered.config import resolve_settings
from remastered.web.cleanup import purge_anonymous_owner_data
from remastered.web.db import JobStore
from remastered.web.identity import RequestIdentity

ANON_VISIT_COOKIE = "remastered_anon_visit"
ANON_VISIT_SESSION_KEY = "anon_visit_id"


def _is_anonymous(identity: RequestIdentity) -> bool:
    return identity.account_id is None


def sync_anonymous_visit(
    request: Request,
    identity: RequestIdentity,
    store: JobStore,
) -> None:
    """Drop prior anonymous jobs when the browser visit id changes (new tab/session)."""
    if not _is_anonymous(identity):
        return

    cookie_visit = (request.cookies.get(ANON_VISIT_COOKIE) or "").strip()
    session_visit = request.session.get(ANON_VISIT_SESSION_KEY)

    if not cookie_visit:
        if not session_visit:
            request.session[ANON_VISIT_SESSION_KEY] = uuid4().hex
        return

    if cookie_visit == session_visit:
        return

    work_dir = resolve_settings().work_dir
    purge_anonymous_owner_data(store, work_dir, identity.anon_id)
    request.session[ANON_VISIT_SESSION_KEY] = cookie_visit


def ensure_identity_with_anon_visit(
    request: Request,
    store: JobStore,
) -> RequestIdentity:
    from remastered.web.identity import resolve_identity

    identity = resolve_identity(request)
    sync_anonymous_visit(request, identity, store)
    return identity
