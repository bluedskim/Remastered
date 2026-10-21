from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException

from remastered.download import inspect_youtube_source
from remastered.web.db import JobStore
from remastered.web.settings import WebSettings


class JobCreateRateLimiter:
    """간단한 per-user 작업 생성 간격 제한."""

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval = min_interval_seconds
        self._last: dict[str, float] = defaultdict(float)
        self._lock = Lock()

    def check(self, user_id: str) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        with self._lock:
            prev = self._last.get(user_id, 0.0)
            if now - prev < self.min_interval:
                wait = int(self.min_interval - (now - prev))
                raise HTTPException(
                    status_code=429,
                    detail=f"요청이 너무 잦습니다. {wait}초 후 다시 시도하세요.",
                )
            self._last[user_id] = now


def enforce_job_limits(store: JobStore, user_id: str, youtube_url: str) -> None:
    ws = WebSettings()
    active = store.count_user_active_jobs(user_id)
    if active >= ws.max_concurrent_jobs_per_user:
        raise HTTPException(
            status_code=429,
            detail="이미 진행 중인 작업이 있습니다. 완료 후 다시 시도하세요.",
        )
    today = store.count_user_jobs_today(user_id)
    if today >= ws.daily_job_quota_per_user:
        raise HTTPException(
            status_code=429,
            detail=f"오늘 작업 한도({ws.daily_job_quota_per_user}회)에 도달했습니다.",
        )
    info = inspect_youtube_source(youtube_url.strip())
    duration = info.get("duration") or 0
    if duration and duration > ws.max_source_duration_seconds:
        raise HTTPException(
            status_code=400,
            detail=(
                f"영상 길이가 제한({ws.max_source_duration_seconds}초)을 초과합니다 "
                f"({int(duration)}초)."
            ),
        )
    size = info.get("filesize") or info.get("filesize_approx") or 0
    if size and size > ws.max_source_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"영상 크기가 제한({ws.max_source_bytes // (1024 * 1024)}MB)을 초과합니다.",
        )
