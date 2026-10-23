from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from remastered.config import RemasteredSettings
from remastered.download import download_audio
from remastered.web.limits import validate_youtube_source_limits
from remastered.web.reference_youtube import is_youtube_reference_job
from remastered.web.settings import WebSettings

REFERENCE_DOWNLOAD_FAILED = "Could not download the reference video."


def _cache_is_fresh(path: Path, retention_days: int) -> bool:
    if not path.is_file():
        return False
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds <= retention_days * 86400


def reference_cache_wav(work_dir: Path, video_id: str) -> Path:
    return work_dir / "reference_cache" / f"{video_id}.wav"


def resolve_reference_wav(
    job: dict,
    settings: RemasteredSettings,
    *,
    log: Callable[[str], None] | None = None,
) -> tuple[Path, str | None]:
    def _log(msg: str) -> None:
        if log:
            log(msg)

    if is_youtube_reference_job(job):
        video_id = (job.get("reference_video_id") or "").strip()
        url = (job.get("reference_youtube_url") or "").strip()
        if not video_id or not url:
            raise RuntimeError(REFERENCE_DOWNLOAD_FAILED)

        ws = WebSettings()
        cache_path = reference_cache_wav(settings.work_dir, video_id)
        if _cache_is_fresh(cache_path, ws.job_retention_days):
            _log(f"Using cached reference audio: {video_id}")
            return cache_path, None

        validate_youtube_source_limits(url)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _log("Downloading reference audio…")
        try:
            result = download_audio(
                url, cache_path.parent, yt_dlp_path=settings.yt_dlp_path
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"{REFERENCE_DOWNLOAD_FAILED} ({exc})") from exc

        wav = result.audio_path
        if not wav.is_file():
            raise RuntimeError(REFERENCE_DOWNLOAD_FAILED)
        return wav, result.title

    reference = Path(job["reference_path"])
    if not reference.is_file():
        raise FileNotFoundError(f"Reference file not found: {reference}")
    return reference, None
