from __future__ import annotations

from remastered.web.job_ui import (
    format_relative_time,
    job_progress_steps,
    youtube_thumbnail_url,
)


def test_youtube_thumbnail_from_video_id() -> None:
    url = youtube_thumbnail_url({"source_video_id": "abc123XYZ01"})
    assert url == "https://i.ytimg.com/vi/abc123XYZ01/hqdefault.jpg"


def test_job_progress_steps_completed() -> None:
    job = {
        "status": "completed",
        "stage": "done",
        "stage_state": {"download": "done", "master": "done"},
    }
    steps = job_progress_steps(job)
    assert steps[-1]["state"] == "done"


def test_format_relative_time_recent() -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    assert format_relative_time(now) == "방금 전"
