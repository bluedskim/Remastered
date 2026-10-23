from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from remastered.stereo_width import stereo_width_label
from remastered.web.reference_display import reference_ui_for_job
from remastered.youtube_url import extract_youtube_video_id

_PROGRESS_LABELS = (
    ("download", "Download"),
    ("master", "Mastering"),
    ("done", "Done"),
)


def youtube_thumbnail_url(job: dict[str, Any]) -> str | None:
    video_id = (job.get("source_video_id") or "").strip()
    if not video_id:
        video_id = extract_youtube_video_id(job.get("youtube_url") or "") or ""
    if not video_id:
        return None
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def format_relative_time(created_at: str) -> str:
    try:
        normalized = created_at.replace("Z", "+00:00")
        if "T" not in normalized and " " in normalized:
            normalized = normalized.replace(" ", "T", 1)
        if "+" not in normalized[-6:] and normalized[-1] != "Z":
            normalized += "+00:00"
        created = datetime.fromisoformat(normalized)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return created_at[:16] if created_at else ""

    now = datetime.now(timezone.utc)
    delta = now - created.astimezone(timezone.utc)
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} hr ago"
    if seconds < 172800:
        return "Yesterday"
    return created.strftime("%Y-%m-%d %H:%M")


def status_pill_class(status: str) -> str:
    return {
        "pending": "pill-pending",
        "running": "pill-running",
        "completed": "pill-completed",
        "failed": "pill-failed",
        "cancelled": "pill-cancelled",
    }.get(status, "pill-neutral")


def job_progress_steps(job: dict[str, Any]) -> list[dict[str, str]]:
    stage_state = job.get("stage_state") or {}
    status = job.get("status") or ""
    steps: list[dict[str, str]] = []

    for key, label in _PROGRESS_LABELS[:-1]:
        state = stage_state.get(key, "pending")
        if status == "failed" and job.get("stage") == key:
            state = "failed"
        steps.append({"key": key, "label": label, "state": state})

    if status == "completed":
        done_state = "done"
    elif status == "failed":
        done_state = "failed"
    elif status == "running" and job.get("stage") == "master" and stage_state.get("master") == "done":
        done_state = "running"
    else:
        done_state = "pending"
    steps.append({"key": "done", "label": "Done", "state": done_state})
    return steps


def enrich_job_ui(job: dict[str, Any]) -> dict[str, Any]:
    data = dict(job)
    created = job.get("created_at") or ""
    data["relative_time"] = format_relative_time(created)
    data["status_pill_class"] = status_pill_class(job.get("status") or "")
    data["progress_steps"] = job_progress_steps(job)
    data["youtube_thumbnail"] = youtube_thumbnail_url(job)
    ref = reference_ui_for_job(job)
    data["reference_kind"] = ref["kind"]
    data["reference_genre"] = ref["genre"]
    data["reference_label"] = ref["label"]
    data["reference_url"] = ref.get("url")
    data["stereo_width_label"] = stereo_width_label(job.get("stereo_width_mode"))
    return data
