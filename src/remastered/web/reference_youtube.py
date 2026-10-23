from __future__ import annotations

from remastered.youtube_url import extract_youtube_video_id, normalize_youtube_url

YOUTUBE_REF_PATH_PREFIX = "youtube-ref:"

INVALID_REFERENCE_URL = "Enter a valid YouTube reference URL."
ENTER_REFERENCE_YOUTUBE_URL = "Enter a reference YouTube URL."
CHOOSE_REFERENCE_FILE = "Choose a reference file."
SELECT_PRESET_REFERENCE = "Select a preset reference track."
SAME_VIDEO_ERROR = (
    "The reference video cannot be the same as the source video."
)

REFERENCE_SOURCE_MODES = frozenset({"preset", "youtube", "upload"})


def youtube_reference_path_key(video_id: str) -> str:
    return f"{YOUTUBE_REF_PATH_PREFIX}{video_id}"


def is_youtube_reference_job(job: dict) -> bool:
    if (job.get("reference_source_type") or "").strip() == "youtube":
        return True
    path = (job.get("reference_path") or "").strip()
    return path.startswith(YOUTUBE_REF_PATH_PREFIX)


def parse_reference_youtube_url(raw: str) -> tuple[str, str]:
    """Return (normalized_watch_url, video_id)."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Reference YouTube URL is empty.")
    video_id = extract_youtube_video_id(text)
    if not video_id:
        raise ValueError(INVALID_REFERENCE_URL)
    return normalize_youtube_url(text), video_id


def ensure_reference_differs_from_source(source_url: str, reference_url: str) -> None:
    source_id = extract_youtube_video_id(normalize_youtube_url(source_url))
    ref_id = extract_youtube_video_id(normalize_youtube_url(reference_url))
    if source_id and ref_id and source_id == ref_id:
        raise ValueError(SAME_VIDEO_ERROR)
