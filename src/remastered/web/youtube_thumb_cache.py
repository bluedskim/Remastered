"""Server-side YouTube thumbnail cache under ``work/thumbs/{video_id}.jpg``.

Thumbnails are shared across jobs. ``cleanup_stale_thumbnails`` removes files
older than ``job_retention_days`` during the periodic job cleanup pass (orphan
files that outlive their jobs are dropped with the same retention window).
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
DOWNLOAD_TIMEOUT_SECONDS = 8.0
THUMB_CACHE_CONTROL = "public, max-age=604800"
PLACEHOLDER_CACHE_CONTROL = "public, max-age=300"

YOUTUBE_THUMB_SOURCES = (
    "https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    "https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
)

THUMB_PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" '
    'viewBox="0 0 320 180" role="img" aria-label="No thumbnail">'
    '<rect width="320" height="180" rx="12" fill="#14101f"/>'
    '<path d="M160 62 L198 90 L160 118 L122 90 Z" fill="#6b7280"/>'
    "</svg>"
).encode("utf-8")


def is_valid_youtube_video_id(video_id: str) -> bool:
    return bool(_VIDEO_ID_RE.fullmatch((video_id or "").strip()))


def thumb_cache_dir(work_dir: Path) -> Path:
    return work_dir / "thumbs"


def thumb_cache_path(work_dir: Path, video_id: str) -> Path:
    return thumb_cache_dir(work_dir) / f"{video_id}.jpg"


def youtube_thumb_media_url(video_id: str) -> str:
    vid = (video_id or "").strip()
    return f"/media/thumb/{vid}.jpg"


def youtube_reference_thumbnail_url(video_id: str) -> str:
    """Same-origin cached thumbnail URL for templates and API responses."""
    vid = (video_id or "").strip()
    if not is_valid_youtube_video_id(vid):
        return ""
    return youtube_thumb_media_url(vid)


def _download_thumb_bytes(video_id: str) -> bytes | None:
    headers = {"User-Agent": "Remastered/1.0 (+https://mastered.baem.app)"}
    with httpx.Client(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        for template in YOUTUBE_THUMB_SOURCES:
            url = template.format(video_id=video_id)
            try:
                response = client.get(url, headers=headers)
                if response.status_code != 200:
                    continue
                body = response.content
                if len(body) < 128:
                    continue
                return body
            except Exception as exc:  # noqa: BLE001
                logger.debug("Thumb download failed for %s via %s: %s", video_id, url, exc)
    return None


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{int(time.time() * 1000)}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_thumb_cached(work_dir: Path, video_id: str) -> bool:
    """Download and store thumbnail when missing. Returns True if a JPEG is on disk."""
    if not is_valid_youtube_video_id(video_id):
        return False
    path = thumb_cache_path(work_dir, video_id)
    if path.is_file() and path.stat().st_size > 0:
        return True
    body = _download_thumb_bytes(video_id)
    if not body:
        return False
    try:
        _atomic_write(path, body)
    except OSError as exc:
        logger.warning("Failed to write thumb cache %s: %s", path, exc)
        return path.is_file()
    return path.is_file()


def cleanup_stale_thumbnails(work_dir: Path, retention_days: int) -> int:
    """Remove thumb files older than retention (orphan cleanup; shared across jobs)."""
    if retention_days <= 0:
        return 0
    root = thumb_cache_dir(work_dir)
    if not root.is_dir():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for path in root.glob("*.jpg"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed
