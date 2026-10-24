from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from remastered.download import inspect_youtube_source
from remastered.web.reference_youtube import parse_reference_youtube_url
from remastered.web.youtube_thumb_cache import youtube_reference_thumbnail_url

logger = logging.getLogger(__name__)

OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
OEMBED_TIMEOUT_SECONDS = 4.0
CACHE_TTL_SECONDS = 300


@dataclass(frozen=True)
class YouTubeVideoMeta:
    video_id: str
    title: str
    author_name: str | None
    thumbnail_url: str
    duration_seconds: int | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "video_id": self.video_id,
            "title": self.title,
            "author_name": self.author_name,
            "thumbnail_url": self.thumbnail_url,
        }
        if self.duration_seconds is not None:
            data["duration_seconds"] = self.duration_seconds
            data["duration_label"] = format_duration_label(self.duration_seconds)
        return data


def format_duration_label(seconds: int) -> str:
    sec = max(0, int(seconds))
    hours, rem = divmod(sec, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


_cache: dict[str, tuple[float, YouTubeVideoMeta]] = {}
_duration_cache: dict[str, tuple[float, int | None]] = {}


def _cache_get(key: str) -> YouTubeVideoMeta | None:
    entry = _cache.get(key)
    if not entry:
        return None
    expires_at, meta = entry
    if time.monotonic() > expires_at:
        _cache.pop(key, None)
        return None
    return meta


def _cache_set(key: str, meta: YouTubeVideoMeta) -> None:
    _cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, meta)


def clear_youtube_meta_cache() -> None:
    _cache.clear()
    _duration_cache.clear()


def _lookup_duration_seconds(normalized_url: str, video_id: str) -> int | None:
    cached = _duration_cache.get(video_id)
    if cached:
        expires_at, value = cached
        if time.monotonic() <= expires_at:
            return value
        _duration_cache.pop(video_id, None)
    try:
        info = inspect_youtube_source(normalized_url)
        raw = info.get("duration")
        duration = int(raw) if raw is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("YouTube duration lookup failed for %s: %s", video_id, exc)
        duration = None
    _duration_cache[video_id] = (time.monotonic() + CACHE_TTL_SECONDS, duration)
    return duration


def fetch_youtube_video_meta(raw_url: str) -> YouTubeVideoMeta:
    """Normalize URL and load metadata from YouTube oEmbed (with TTL cache)."""
    normalized, video_id = parse_reference_youtube_url(raw_url)
    cached = _cache_get(video_id)
    if cached:
        if cached.duration_seconds is None:
            duration = _lookup_duration_seconds(normalized, video_id)
            if duration is not None:
                cached = YouTubeVideoMeta(
                    video_id=cached.video_id,
                    title=cached.title,
                    author_name=cached.author_name,
                    thumbnail_url=cached.thumbnail_url,
                    duration_seconds=duration,
                )
                _cache_set(video_id, cached)
        return cached

    request_url = f"{OEMBED_ENDPOINT}?url={quote(normalized, safe='')}&format=json"
    try:
        with httpx.Client(timeout=OEMBED_TIMEOUT_SECONDS) as client:
            response = client.get(request_url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("YouTube oEmbed failed for %s: %s", video_id, exc)
        raise RuntimeError("Couldn't load video info.") from exc

    title = (payload.get("title") or "").strip() or f"YouTube video {video_id}"
    author = (payload.get("author_name") or "").strip() or None
    thumb = youtube_reference_thumbnail_url(video_id)
    duration = _lookup_duration_seconds(normalized, video_id)
    meta = YouTubeVideoMeta(
        video_id=video_id,
        title=title,
        author_name=author,
        thumbnail_url=thumb,
        duration_seconds=duration,
    )
    _cache_set(video_id, meta)
    return meta


def try_fetch_youtube_video_meta(raw_url: str) -> YouTubeVideoMeta | None:
    try:
        return fetch_youtube_video_meta(raw_url)
    except ValueError:
        return None
    except RuntimeError:
        return None
