from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from remastered.web.db import JobStore
from remastered.web.settings import WebSettings

logger = logging.getLogger(__name__)


def cleanup_old_jobs(store: JobStore, work_dir: Path) -> int:
    ws = WebSettings()
    if ws.job_retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=ws.job_retention_days)
    removed = 0
    for job in store.list_jobs_for_cleanup(cutoff.isoformat()):
        for key in ("audio_path", "mastered_path", "video_path"):
            path = job.get(key)
            if path:
                p = Path(path)
                if p.is_file():
                    p.unlink(missing_ok=True)
        job_root = work_dir / "users" / job["user_id"] / job.get("source_video_id", "")
        if job_root.is_dir() and job.get("source_video_id"):
            shutil.rmtree(job_root, ignore_errors=True)
        store.mark_files_cleaned(job["id"])
        removed += 1
    if removed:
        logger.info("오래된 작업 파일 %d건 정리", removed)
    return removed
