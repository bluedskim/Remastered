from __future__ import annotations

from fastapi import HTTPException

from remastered.download import inspect_youtube_source
from remastered.web.settings import WebSettings


def validate_youtube_source_limits(youtube_url: str) -> None:
    """워커: 길이·크기 제한 (yt-dlp 메타데이터 조회)."""
    ws = WebSettings()
    info = inspect_youtube_source(youtube_url.strip())
    duration = info.get("duration") or 0
    if duration and duration > ws.max_source_duration_seconds:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Video length exceeds the limit ({ws.max_source_duration_seconds}s); "
                f"got {int(duration)}s."
            ),
        )
    size = info.get("filesize") or info.get("filesize_approx") or 0
    if size and size > ws.max_source_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Video size exceeds the limit "
                f"({ws.max_source_bytes // (1024 * 1024)} MB)."
            ),
        )
