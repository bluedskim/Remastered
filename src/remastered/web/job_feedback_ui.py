from __future__ import annotations

from pathlib import Path
from typing import Any

from remastered.config import resolve_settings
from remastered.web.db import JobStore
from remastered.web.identity import RequestIdentity
from remastered.web.media import media_exists
from remastered.web.video_download_access import trusted_account_email


def _feedback_media_ok(job: dict[str, Any], work_dir: Path) -> bool:
    audio_ok = media_exists(job.get("audio_path"), job, work_dir)
    mastered_ok = media_exists(job.get("mastered_path"), job, work_dir)
    return audio_ok or mastered_ok


def enrich_jobs_list_feedback(
    jobs: list[dict[str, Any]],
    *,
    identity: RequestIdentity,
    store: JobStore,
) -> None:
    """Attach feedback_show and feedback_user_rating for home / list API rows."""
    if not identity.account_id:
        for job in jobs:
            job["feedback_show"] = False
            job["feedback_user_rating"] = None
        return

    work_dir = resolve_settings().work_dir
    eligible_ids: list[str] = []
    for job in jobs:
        show = (job.get("status") or "") == "completed" and _feedback_media_ok(
            job, work_dir
        )
        if show:
            eligible_ids.append(job["id"])

    ratings = store.get_job_feedback_ratings_for_user(
        identity.account_id, eligible_ids
    )

    for job in jobs:
        show = job["id"] in eligible_ids
        job["feedback_show"] = show
        job["feedback_user_rating"] = ratings.get(job["id"]) if show else None


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
