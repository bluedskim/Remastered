from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from remastered.web.cleanup import cleanup_old_jobs
from remastered.web.db import JobStore
from remastered.web.job_media_paths import job_workspace_dir
from remastered.web.job_video_download import (
    BUILT_VIDEO_FILENAME,
    init_video_build_manager,
)
from remastered.web.youtube_thumb_cache import thumb_cache_dir, thumb_cache_path


def _set_created_at(store: JobStore, job_id: str, created_at: str) -> None:
    with store._conn() as conn:
        conn.execute(
            "UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (created_at, created_at, job_id),
        )


def test_cleanup_old_jobs_runs_thumbnail_and_video_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end cleanup_old_jobs must resolve all helpers (regression guard)."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("REMASTERED_WEB_JOB_RETENTION_DAYS", "7")
    monkeypatch.setenv("REMASTERED_WEB_VIDEO_DOWNLOAD_CACHE_TTL_SECONDS", "1")

    store = JobStore(work / "remastered_jobs.db")
    user_id = "user-cleanup-test"
    job_id = store.create_job(
        user_id=user_id,
        youtube_url="https://www.youtube.com/watch?v=abc12345678",
        reference_path="/references/ref.wav",
        title="Old job",
        description="",
        privacy="private",
        upload_enabled=False,
    )
    store.update_job(job_id, status="completed", stage="done")

    old_created = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat().replace("+00:00", "Z")
    _set_created_at(store, job_id, old_created)

    ws = job_workspace_dir(work, user_id, job_id)
    ws.mkdir(parents=True)
    audio = ws / "source.wav"
    mastered = ws / "master.wav"
    audio.write_bytes(b"audio")
    mastered.write_bytes(b"master")
    store.update_job(
        job_id,
        audio_path=str(audio),
        mastered_path=str(mastered),
    )

    stale_thumb = thumb_cache_path(work, "abc12345678")
    stale_thumb.parent.mkdir(parents=True, exist_ok=True)
    stale_thumb.write_bytes(b"jpeg-bytes")
    stale_mtime = time.time() - (10 * 86400)
    os.utime(stale_thumb, (stale_mtime, stale_mtime))

    mgr = init_video_build_manager(work)
    built_video = ws / BUILT_VIDEO_FILENAME
    built_video.write_bytes(b"cached-mp4")
    from remastered.web.job_video_download import VideoBuildPhase

    with mgr._lock:
        state = mgr._state_for(job_id)
        state.phase = VideoBuildPhase.READY
        state.expires_at = time.monotonic() - 1
        state.output_path = built_video

    removed_jobs = cleanup_old_jobs(store, work)
    assert removed_jobs == 1

    job = store.get_job(job_id)
    assert job is not None
    assert job["files_cleaned"] == 1
    assert not audio.is_file()
    assert not mastered.is_file()
    assert not built_video.is_file()
    assert not stale_thumb.is_file()
    assert thumb_cache_dir(work).is_dir() or not list(thumb_cache_dir(work).glob("*.jpg"))
