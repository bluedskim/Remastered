from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from remastered.web.db import JobStore
from remastered.web.job_media_paths import delete_job_workspace
from remastered.web.settings import WebSettings
from remastered.web.stream_audio import playback_path_for_wav
from remastered.web.youtube_thumb_cache import cleanup_stale_thumbnails

logger = logging.getLogger(__name__)


def delete_job_media_files(job: dict, work_dir: Path) -> None:
    work_dir = Path(work_dir)
    for key in ("audio_path", "mastered_path", "video_path"):
        path = job.get(key)
        if not path:
            continue
        p = Path(path)
        if p.is_file():
            p.unlink(missing_ok=True)
        playback = playback_path_for_wav(p)
        if playback.is_file():
            playback.unlink(missing_ok=True)
    delete_job_workspace(job, work_dir)


def purge_anonymous_owner_data(
    store: JobStore,
    work_dir: Path,
    owner_id: str,
) -> int:
    """Remove all jobs and uploaded references for an anonymous owner id."""
    if not owner_id.startswith("anon-"):
        return 0
    jobs = store.delete_all_jobs_for_user(owner_id)
    for job in jobs:
        delete_job_media_files(job, work_dir)
    ref_dir = work_dir / "users" / owner_id / "references"
    if ref_dir.is_dir():
        shutil.rmtree(ref_dir, ignore_errors=True)
    if jobs:
        logger.info("anonymous visit reset: removed %d job(s) for %s", len(jobs), owner_id)
    return len(jobs)


def cleanup_old_jobs(store: JobStore, work_dir: Path) -> int:
    ws = WebSettings()
    if ws.job_retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=ws.job_retention_days)
    removed = 0
    for job in store.list_jobs_for_cleanup(cutoff.isoformat()):
        delete_job_media_files(job, work_dir)
        store.mark_files_cleaned(job["id"])
        removed += 1
    thumb_removed = cleanup_stale_thumbnails(work_dir, ws.job_retention_days)
    if removed:
        logger.info("오래된 작업 파일 %d건 정리", removed)
    if thumb_removed:
        logger.info("오래된 썸네일 캐시 %d건 정리", thumb_removed)
    return removed
