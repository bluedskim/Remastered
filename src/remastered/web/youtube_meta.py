from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from remastered.web.reference_youtube import parse_reference_youtube_url

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "author_name": self.author_name,
            "thumbnail_url": self.thumbnail_url,
        }


def youtube_reference_thumbnail_url(video_id: str) -> str:
    vid = (video_id or "").strip()
    return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"


_cache: dict[str, tuple[float, YouTubeVideoMeta]] = {}


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


def fetch_youtube_video_meta(raw_url: str) -> YouTubeVideoMeta:
    """Normalize URL and load metadata from YouTube oEmbed (with TTL cache)."""
    normalized, video_id = parse_reference_youtube_url(raw_url)
    cached = _cache_get(video_id)
    if cached:
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
    meta = YouTubeVideoMeta(
        video_id=video_id,
        title=title,
        author_name=author,
        thumbnail_url=thumb,
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
