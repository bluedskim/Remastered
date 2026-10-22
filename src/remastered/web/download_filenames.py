from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from remastered.youtube_url import extract_youtube_video_id

MAX_DOWNLOAD_FILENAME_LEN = 150

_ILLEGAL_FILENAME_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def sanitize_filename_stem(text: str) -> str:
    cleaned = _ILLEGAL_FILENAME_RE.sub("", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _id_fallback_stem(job: dict[str, Any]) -> str:
    video_id = (job.get("source_video_id") or "").strip()
    if video_id:
        stem = sanitize_filename_stem(video_id)
        if stem:
            return stem
    yt_id = extract_youtube_video_id(job.get("youtube_url") or "")
    if yt_id:
        return yt_id
    job_id = (job.get("id") or "").strip()
    return job_id or "download"


def resolve_download_title_stem(job: dict[str, Any]) -> str:
    title = sanitize_filename_stem((job.get("title") or "").strip())
    if title:
        return title
    return _id_fallback_stem(job)


def _kind_label_and_ext(kind: str, media_path: Path) -> tuple[str, str]:
    label = "(Original)" if kind == "audio" else "(Remastered)"
    ext = media_path.suffix if media_path.suffix else ".wav"
    return label, ext


def media_download_filename(job: dict[str, Any], kind: str, media_path: Path) -> str:
    """Human-facing UTF-8 download basename (no directory components)."""
    label, ext = _kind_label_and_ext(kind, media_path)
    suffix = f" {label}{ext}"
    max_stem_len = max(1, MAX_DOWNLOAD_FILENAME_LEN - len(suffix))
    stem = resolve_download_title_stem(job)
    if len(stem) > max_stem_len:
        stem = stem[:max_stem_len].rstrip()
    if not stem:
        stem = _id_fallback_stem(job)[:max_stem_len].rstrip() or "download"
    return f"{stem}{suffix}"


def _ascii_fallback_filename(
    utf8_filename: str,
    job: dict[str, Any],
    kind: str,
    media_path: Path,
) -> str:
    label, ext = _kind_label_and_ext(kind, media_path)
    suffix = f" {label}{ext}"
    utf8_stem = utf8_filename[: -len(suffix)] if utf8_filename.endswith(suffix) else utf8_filename

    if any(ord(ch) >= 128 for ch in utf8_stem):
        stem = _id_fallback_stem(job)
    else:
        ascii_chars: list[str] = []
        for ch in utf8_stem:
            if ord(ch) < 32 or ch in '/\\:*?"<>|':
                continue
            if ord(ch) < 128:
                ascii_chars.append(ch)
            else:
                ascii_chars.append("_")
        stem = re.sub(r"\s+", " ", "".join(ascii_chars)).strip()
        stem = stem.strip("._-")

    max_stem_len = max(1, MAX_DOWNLOAD_FILENAME_LEN - len(suffix))
    if len(stem) > max_stem_len:
        stem = stem[:max_stem_len].rstrip()
    if not stem:
        stem = _id_fallback_stem(job)[:max_stem_len].rstrip() or "download"
    return f"{stem}{suffix}"


def build_attachment_content_disposition(
    job: dict[str, Any],
    kind: str,
    media_path: Path,
) -> str:
    utf8_name = media_download_filename(job, kind, media_path)
    ascii_name = _ascii_fallback_filename(utf8_name, job, kind, media_path)
    encoded = quote(utf8_name, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'
