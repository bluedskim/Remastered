from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse

_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

_CANONICAL_WATCH = "https://www.youtube.com/watch"


def extract_youtube_video_id(url: str) -> str | None:
    """Return an 11-character YouTube video id when the URL clearly refers to one video."""
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    path = parsed.path or ""
    segments = [s for s in path.split("/") if s]

    if host == "youtu.be" and segments:
        candidate = segments[0].split("?")[0]
        return candidate if _VIDEO_ID_RE.fullmatch(candidate) else None

    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if path.startswith("/watch"):
            qs = parse_qs(parsed.query, keep_blank_values=False)
            vid = (qs.get("v") or [None])[0]
            if vid and _VIDEO_ID_RE.fullmatch(vid):
                return vid
            return None
        if len(segments) >= 2 and segments[0] in {"shorts", "embed", "live"}:
            candidate = segments[1]
            return candidate if _VIDEO_ID_RE.fullmatch(candidate) else None
        if len(segments) == 1 and segments[0] == "watch" and parsed.query:
            qs = parse_qs(parsed.query, keep_blank_values=False)
            vid = (qs.get("v") or [None])[0]
            if vid and _VIDEO_ID_RE.fullmatch(vid):
                return vid

    return None


def normalize_youtube_url(url: str) -> str:
    """
    Canonicalize watch-style URLs to https://www.youtube.com/watch?v=<id>.
    Strips playlist/radio tracking params (list, index, start_radio, si, …).
    Preserves start offset ``t`` when present.
    """
    stripped = (url or "").strip()
    video_id = extract_youtube_video_id(stripped)
    if not video_id:
        return stripped

    parsed = urlparse(stripped)
    qs = parse_qs(parsed.query, keep_blank_values=False)
    params: dict[str, str] = {"v": video_id}
    t_vals = qs.get("t")
    if t_vals and t_vals[0]:
        params["t"] = t_vals[0]

    return f"{_CANONICAL_WATCH}?{urlencode(params)}"
