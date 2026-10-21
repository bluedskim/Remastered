from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException

from remastered.download import inspect_youtube_source
from remastered.web.db import JobStore
from remastered.web.settings import WebSettings


class JobCreateRateLimiter:
    """Per-owner 및 per-IP 작업 생성 간격 제한."""

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval = min_interval_seconds
        self._last: dict[str, float] = defaultdict(float)
        self._lock = Lock()

    def _check_key(self, key: str) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        with self._lock:
            prev = self._last.get(key, 0.0)
            if now - prev < self.min_interval:
                wait = int(self.min_interval - (now - prev))
                raise HTTPException(
                    status_code=429,
                    detail=f"요청이 너무 잦습니다. {wait}초 후 다시 시도하세요.",
                )
            self._last[key] = now

    def check(self, owner_id: str, client_ip: str) -> None:
        self._check_key(f"owner:{owner_id}")
        if client_ip and client_ip != "unknown":
            self._check_key(f"ip:{client_ip}")


def _enforce_concurrency_and_quota(
    store: JobStore,
    ws: WebSettings,
    *,
    owner_id: str,
    client_ip: str,
) -> None:
    active_owner = store.count_user_active_jobs(owner_id)
    if active_owner >= ws.max_concurrent_jobs_per_user:
        raise HTTPException(
            status_code=429,
            detail="이미 진행 중인 작업이 있습니다. 완료 후 다시 시도하세요.",
        )
    active_ip = store.count_ip_active_jobs(client_ip)
    if active_ip >= ws.max_concurrent_jobs_per_user:
        raise HTTPException(
            status_code=429,
            detail="이미 진행 중인 작업이 있습니다. 완료 후 다시 시도하세요.",
        )
    today_owner = store.count_user_jobs_today(owner_id)
    if today_owner >= ws.daily_job_quota_per_user:
        raise HTTPException(
            status_code=429,
            detail=f"오늘 작업 한도({ws.daily_job_quota_per_user}회)에 도달했습니다.",
        )
    today_ip = store.count_ip_jobs_today(client_ip)
    if today_ip >= ws.daily_job_quota_per_user:
        raise HTTPException(
            status_code=429,
            detail=f"오늘 작업 한도({ws.daily_job_quota_per_user}회)에 도달했습니다.",
        )


def enforce_job_creation_limits(
    store: JobStore,
    owner_id: str,
    client_ip: str,
) -> None:
    """POST /jobs: quota·동시성만 (yt-dlp는 워커에서 검증)."""
    ws = WebSettings()
    _enforce_concurrency_and_quota(store, ws, owner_id=owner_id, client_ip=client_ip)


def validate_youtube_source_limits(youtube_url: str) -> None:
    """워커: 길이·크기 제한 (yt-dlp 메타데이터 조회)."""
    ws = WebSettings()
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


def enforce_job_limits(
    store: JobStore,
    owner_id: str,
    client_ip: str,
    youtube_url: str,
) -> None:
    """하위 호환: 생성 한도 + 소스 검증."""
    enforce_job_creation_limits(store, owner_id, client_ip)
    validate_youtube_source_limits(youtube_url)
