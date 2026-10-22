from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp

from remastered.paths import ensure_dir
from remastered.youtube_url import normalize_youtube_url

logger = logging.getLogger(__name__)

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

DEFAULT_SOCKET_TIMEOUT = 30
DEFAULT_INSPECT_TIMEOUT_SECONDS = 120
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = int(
    os.environ.get("REMASTERED_YTDLP_DOWNLOAD_TIMEOUT", "600")
)

_PLAYLIST_UNSUPPORTED = (
    "YouTube playlists and Mix radio are not supported. Use a single video URL."
)


def base_ydl_opts(**extra: Any) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "noplaylist": True,
        "socket_timeout": DEFAULT_SOCKET_TIMEOUT,
    }
    opts.update(extra)
    return opts


def _coerce_single_video_info(info: dict[str, Any]) -> dict[str, Any]:
    if info.get("_type") not in ("playlist", "multi_video") and "entries" not in info:
        return info

    entries = info.get("entries") or []
    resolved: list[dict[str, Any]] = []
    for entry in entries:
        if entry is None:
            continue
        if isinstance(entry, dict):
            resolved.append(entry)

    if not resolved:
        raise RuntimeError(_PLAYLIST_UNSUPPORTED)

    if len(resolved) > 1:
        logger.warning(
            "yt-dlp returned %d entries for a single-video request; using the first only",
            len(resolved),
        )

    first = resolved[0]
    if first.get("_type") in ("playlist", "multi_video") or "entries" in first:
        raise RuntimeError(_PLAYLIST_UNSUPPORTED)
    return first


def _extract_info_with_ydl(
    ydl_opts: dict[str, Any],
    url: str,
    *,
    download: bool,
) -> dict[str, Any]:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=download)
    if info is None:
        raise RuntimeError(f"Could not fetch video info: {url}")
    return _coerce_single_video_info(info)


def _run_timed(
    ydl_opts: dict[str, Any],
    url: str,
    *,
    download: bool,
    timeout_seconds: float | None,
) -> dict[str, Any]:
    if timeout_seconds is None:
        return _extract_info_with_ydl(ydl_opts, url, download=download)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_extract_info_with_ydl, ydl_opts, url, download=download)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError:
            raise RuntimeError(
                f"YouTube {'download' if download else 'lookup'} timed out after "
                f"{int(timeout_seconds)} seconds."
            ) from None


def inspect_youtube_source(url: str) -> dict[str, Any]:
    """다운로드 없이 영상 메타데이터만 조회합니다 (길이·크기 제한용)."""
    canonical = normalize_youtube_url(url)
    ydl_opts = base_ydl_opts(
        quiet=True,
        no_warnings=True,
        skip_download=True,
    )
    return _run_timed(
        ydl_opts,
        canonical,
        download=False,
        timeout_seconds=DEFAULT_INSPECT_TIMEOUT_SECONDS,
    )


@dataclass
class DownloadResult:
    video_id: str
    title: str
    audio_path: Path
    thumbnail_path: Path | None
    source_url: str


def _pick_thumbnail(out_dir: Path, video_id: str) -> Path | None:
    for ext in (".jpg", ".jpeg", ".webp", ".png"):
        candidate = out_dir / f"{video_id}{ext}"
        if candidate.is_file():
            return candidate
    for pattern in (f"{video_id}.*",):
        for path in sorted(out_dir.glob(pattern)):
            if path.suffix.lower() in {".jpg", ".jpeg", ".webp", ".png"}:
                return path
    return None


def download_audio(
    url: str,
    out_dir: Path,
    *,
    yt_dlp_path: str | None = None,
) -> DownloadResult:
    """YouTube URL에서 WAV 오디오와 썸네일을 내려받습니다."""
    canonical = normalize_youtube_url(url)
    ensure_dir(out_dir)
    outtmpl = str(out_dir / "%(id)s.%(ext)s")

    ydl_opts = base_ydl_opts(
        format="bestaudio/best",
        outtmpl=outtmpl,
        writethumbnail=True,
        postprocessors=[
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "0",
            },
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
        quiet=False,
        no_warnings=False,
        ffmpeg_location=None,
    )
    if yt_dlp_path:
        ydl_opts["ffmpeg_location"] = yt_dlp_path

    info = _run_timed(
        ydl_opts,
        canonical,
        download=True,
        timeout_seconds=DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    )
    video_id = info.get("id") or "unknown"
    title = info.get("title") or video_id

    audio_path = out_dir / f"{video_id}.wav"
    if not audio_path.is_file():
        wav_candidates = list(out_dir.glob(f"{video_id}*.wav"))
        if not wav_candidates:
            raise FileNotFoundError(f"Downloaded WAV not found in: {out_dir}")
        audio_path = wav_candidates[0]

    thumb_path = _pick_thumbnail(out_dir, video_id)
    logger.info("다운로드 완료: %s -> %s", title, audio_path)
    return DownloadResult(
        video_id=video_id,
        title=title,
        audio_path=audio_path,
        thumbnail_path=thumb_path,
        source_url=canonical,
    )
