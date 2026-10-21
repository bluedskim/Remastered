from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp

from remastered.paths import ensure_dir

logger = logging.getLogger(__name__)

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def inspect_youtube_source(url: str) -> dict[str, Any]:
    """다운로드 없이 영상 메타데이터만 조회합니다 (길이·크기 제한용)."""
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError(f"영상 정보를 가져올 수 없습니다: {url}")
    return info


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
    ensure_dir(out_dir)
    outtmpl = str(out_dir / "%(id)s.%(ext)s")

    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "writethumbnail": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "0",
            },
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
        ],
        "quiet": False,
        "no_warnings": False,
        "ffmpeg_location": None,
    }
    if yt_dlp_path:
        ydl_opts["ffmpeg_location"] = yt_dlp_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError(f"영상 정보를 가져올 수 없습니다: {url}")
        video_id = info.get("id") or "unknown"
        title = info.get("title") or video_id

    audio_path = out_dir / f"{video_id}.wav"
    if not audio_path.is_file():
        wav_candidates = list(out_dir.glob(f"{video_id}*.wav"))
        if not wav_candidates:
            raise FileNotFoundError(f"다운로드된 WAV를 찾을 수 없습니다: {out_dir}")
        audio_path = wav_candidates[0]

    thumb_path = _pick_thumbnail(out_dir, video_id)
    logger.info("다운로드 완료: %s -> %s", title, audio_path)
    return DownloadResult(
        video_id=video_id,
        title=title,
        audio_path=audio_path,
        thumbnail_path=thumb_path,
        source_url=url,
    )
