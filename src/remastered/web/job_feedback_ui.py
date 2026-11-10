from __future__ import annotations

from typing import Any

from remastered.web.db import JobStore
from remastered.web.identity import RequestIdentity
from remastered.web.video_download_access import trusted_account_email


def job_feedback_template_context(
    job: dict[str, Any],
    *,
    identity: RequestIdentity,
    store: JobStore,
    audio_ok: bool,
    mastered_ok: bool,
) -> dict[str, Any]:
    """Template flags for completed-job thumbs UI."""
    show = bool(
        identity.account_id
        and (job.get("status") or "") == "completed"
        and (audio_ok or mastered_ok)
    )
    if not show:
        return {
            "feedback_show": False,
            "feedback_user_rating": None,
        }
    user_rating = store.get_job_feedback_rating(job["id"], identity.account_id)
    return {
        "feedback_show": True,
        "feedback_user_rating": user_rating,
    }


def enrich_feedback_rows_for_admin(
    rows: list[dict[str, Any]],
    store: JobStore,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        user_id = row.get("user_id") or ""
        email = None
        auth_row = store.get_auth_user_by_id(user_id)
        if auth_row:
            email = (auth_row.get("login_id") or "").strip()
        if not email:
            legacy = store.get_user(user_id)
            if legacy:
                email = (legacy.get("email") or "").strip()
        data["user_email"] = email or user_id
        data["rating_label"] = "Up" if int(row["rating"]) == 1 else "Down"
        enriched.append(data)
    return enriched


def admin_identity_email(identity: RequestIdentity, store: JobStore) -> str | None:
    return trusted_account_email(identity, store)
