from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yt_dlp

from remastered.config import resolve_settings
from remastered.download import (
    base_ydl_opts,
    inspect_youtube_source,
)
from remastered.web.download_filenames import video_remastered_download_filename
from remastered.web.job_media_paths import job_workspace_dir
from remastered.web.job_storage import job_media_storage_owner_id
from remastered.web.limits import validate_youtube_source_limits
from remastered.web.media import media_exists
from remastered.web.settings import WebSettings
from remastered.youtube_url import extract_youtube_video_id, normalize_youtube_url

logger = logging.getLogger(__name__)

BUILT_VIDEO_FILENAME = "remastered_download.mp4"
VIDEO_BUILD_TMP_DIRNAME = "_video_build_tmp"

YTDLP_VIDEO_FORMAT = (
    "bestvideo[height<=1080][ext=mp4]/"
    "bestvideo[height<=1080]/"
    "best[height<=1080][ext=mp4]/"
    "best[height<=1080]"
)


class VideoBuildPhase(str, Enum):
    IDLE = "idle"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"


@dataclass
class VideoBuildState:
    phase: VideoBuildPhase = VideoBuildPhase.IDLE
    error: str | None = None
    started_at: float | None = None
    ready_at: float | None = None
    expires_at: float | None = None
    download_filename: str | None = None
    building: bool = False
    output_path: Path | None = None


def job_is_youtube_sourced(job: dict[str, Any]) -> bool:
    url = (job.get("youtube_url") or "").strip()
    if not url:
        return False
    return extract_youtube_video_id(url) is not None


def job_video_download_eligible(job: dict[str, Any], work_dir: Path) -> bool:
    if (job.get("status") or "") != "completed":
        return False
    if not job_is_youtube_sourced(job):
        return False
    return media_exists(job.get("mastered_path"), job, work_dir)


def built_video_path(job: dict[str, Any], work_dir: Path) -> Path:
    owner = job_media_storage_owner_id(job, work_dir)
    job_id = (job.get("id") or "").strip()
    return job_workspace_dir(work_dir, owner, job_id) / BUILT_VIDEO_FILENAME


def _video_build_tmp_dir(output_path: Path) -> Path:
    return output_path.parent / VIDEO_BUILD_TMP_DIRNAME


def _cleanup_tmp_dir(tmp_dir: Path) -> None:
    if tmp_dir.is_dir():
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _pick_downloaded_video(tmp_dir: Path, video_id: str) -> Path:
    for ext in (".mp4", ".webm", ".mkv", ".mov"):
        candidate = tmp_dir / f"{video_id}{ext}"
        if candidate.is_file():
            return candidate
    matches = sorted(tmp_dir.glob(f"{video_id}.*"))
    for path in matches:
        if path.is_file() and path.suffix.lower() in {
            ".mp4",
            ".webm",
            ".mkv",
            ".mov",
            ".m4v",
        }:
            return path
    any_video = sorted(
        p
        for p in tmp_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
    )
    if any_video:
        return any_video[0]
    raise FileNotFoundError(f"No downloaded video file in {tmp_dir}")


def download_youtube_video_stream(
    url: str,
    out_dir: Path,
    *,
    yt_dlp_path: str | None = None,
) -> Path:
    """Download best-effort MP4-compatible video (no audio) for remux."""
    canonical = normalize_youtube_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    ydl_opts = base_ydl_opts(
        format=YTDLP_VIDEO_FORMAT,
        outtmpl=outtmpl,
        quiet=True,
        no_warnings=True,
    )
    if yt_dlp_path:
        ydl_opts["ffmpeg_location"] = yt_dlp_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(canonical, download=True)
    if info is None:
        raise RuntimeError(f"Could not fetch video: {url}")
    video_id = info.get("id") or "unknown"
    return _pick_downloaded_video(out_dir, video_id)


def mux_remastered_audio_into_video(
    video_path: Path,
    mastered_wav: Path,
    output_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(mastered_wav),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mux failed (code {proc.returncode}): {proc.stderr[-2000:]}"
        )
    return output_path


def run_video_build(
    job: dict[str, Any],
    *,
    work_dir: Path | None = None,
    settings: WebSettings | None = None,
) -> Path:
    """Blocking build: yt-dlp video + ffmpeg remux. Raises on failure."""
    ws = settings or WebSettings()
    work = work_dir or resolve_settings().work_dir
    validate_youtube_source_limits(job["youtube_url"])
    mastered = Path(job["mastered_path"])
    if not media_exists(str(mastered), job, work):
        raise RuntimeError("Remastered audio is unavailable for this job.")

    output_path = built_video_path(job, work)
    tmp_dir = _video_build_tmp_dir(output_path)
    _cleanup_tmp_dir(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    remastered_settings = resolve_settings()
    try:
        video_only = download_youtube_video_stream(
            job["youtube_url"],
            tmp_dir,
            yt_dlp_path=remastered_settings.yt_dlp_path,
        )
        if output_path.is_file():
            output_path.unlink(missing_ok=True)
        mux_remastered_audio_into_video(
            video_only,
            mastered.resolve(),
            output_path,
            ffmpeg_path=remastered_settings.ffmpeg_path,
        )
    finally:
        _cleanup_tmp_dir(tmp_dir)

    if not output_path.is_file():
        raise RuntimeError("Video build finished without output file.")
    return output_path


class VideoBuildManager:
    """In-memory coordinator for on-demand remastered video downloads."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = Path(work_dir)
        ws = WebSettings()
        self._cache_ttl = float(ws.video_download_cache_ttl_seconds)
        self._global_limit = threading.Semaphore(ws.max_concurrent_video_builds)
        self._lock = threading.RLock()
        self._states: dict[str, VideoBuildState] = {}

    def _state_for(self, job_id: str) -> VideoBuildState:
        if job_id not in self._states:
            self._states[job_id] = VideoBuildState()
        return self._states[job_id]

    def discard_job(self, job_id: str) -> None:
        with self._lock:
            state = self._states.pop(job_id, None)
        if state and state.phase == VideoBuildPhase.BUILDING:
            logger.info("Video build discarded for deleted job %s", job_id)

    def note_download_served(self, job: dict[str, Any]) -> None:
        job_id = (job.get("id") or "").strip()
        if not job_id:
            return
        with self._lock:
            state = self._states.get(job_id)
            if not state or state.phase != VideoBuildPhase.READY:
                return
            state.expires_at = time.monotonic() + 120.0

    def cleanup_expired_files(self) -> int:
        removed = 0
        now = time.monotonic()
        with self._lock:
            expired: list[tuple[str, Path | None]] = []
            for job_id, state in self._states.items():
                if state.expires_at is None or state.expires_at > now:
                    continue
                if state.phase not in (VideoBuildPhase.READY, VideoBuildPhase.FAILED):
                    continue
                expired.append((job_id, state.output_path))
                self._reset_state_locked(job_id, state)
        for job_id, path in expired:
            if path and path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
            elif path:
                tmp = _video_build_tmp_dir(path)
                _cleanup_tmp_dir(tmp)
            logger.debug("Expired cached video removed for job %s", job_id)
        return removed

    def _reset_state_locked(self, job_id: str, state: VideoBuildState) -> None:
        state.phase = VideoBuildPhase.IDLE
        state.error = None
        state.started_at = None
        state.ready_at = None
        state.expires_at = None
        state.download_filename = None
        state.building = False
        state.output_path = None

    def _unlink_built_video(self, job: dict[str, Any]) -> None:
        path = built_video_path(job, self.work_dir)
        if path.is_file():
            path.unlink(missing_ok=True)
        tmp = _video_build_tmp_dir(path)
        _cleanup_tmp_dir(tmp)

    def status_payload(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = (job.get("id") or "").strip()
        output = built_video_path(job, self.work_dir)
        with self._lock:
            state = self._state_for(job_id)
            phase = state.phase
            if (
                phase == VideoBuildPhase.IDLE
                and output.is_file()
                and state.expires_at
                and state.expires_at > time.monotonic()
            ):
                phase = VideoBuildPhase.READY
            if phase == VideoBuildPhase.READY and not output.is_file():
                self._reset_state_locked(job_id, state)
                phase = VideoBuildPhase.IDLE

            return {
                "status": phase.value,
                "error": state.error,
                "download_url": (
                    f"/media/{job_id}/video?download=1"
                    if phase == VideoBuildPhase.READY and output.is_file()
                    else None
                ),
                "download_filename": state.download_filename,
            }

    def start_build(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = (job.get("id") or "").strip()
        if not job_id:
            raise ValueError("Missing job id.")
        if not job_video_download_eligible(job, self.work_dir):
            raise ValueError("Video download is not available for this job.")

        with self._lock:
            state = self._state_for(job_id)
            output = built_video_path(job, self.work_dir)
            now = time.monotonic()
            if (
                state.phase == VideoBuildPhase.READY
                and output.is_file()
                and state.expires_at
                and state.expires_at > now
            ):
                return self.status_payload(job)
            if state.building or state.phase == VideoBuildPhase.BUILDING:
                return self.status_payload(job)

            try:
                validate_youtube_source_limits(job["youtube_url"])
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                message = detail if isinstance(detail, str) else str(exc)
                state.phase = VideoBuildPhase.FAILED
                state.error = message
                state.building = False
                return self.status_payload(job)

            self._unlink_built_video(job)
            state.phase = VideoBuildPhase.BUILDING
            state.error = None
            state.started_at = now
            state.ready_at = None
            state.expires_at = None
            state.download_filename = None
            state.building = True

        thread = threading.Thread(
            target=self._run_build_thread,
            args=(dict(job),),
            name=f"video-build-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.status_payload(job)

    def _run_build_thread(self, job: dict[str, Any]) -> None:
        from fastapi import HTTPException

        job_id = (job.get("id") or "").strip()
        with self._global_limit:
            try:
                run_video_build(job, work_dir=self.work_dir)
                filename = video_remastered_download_filename(job)
                output = built_video_path(job, self.work_dir)
                now = time.monotonic()
                with self._lock:
                    state = self._state_for(job_id)
                    state.phase = VideoBuildPhase.READY
                    state.error = None
                    state.ready_at = now
                    state.expires_at = now + self._cache_ttl
                    state.download_filename = filename
                    state.output_path = output
                    state.building = False
            except HTTPException as exc:
                message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                self._fail_build(job, job_id, message)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Video build failed for job %s: %s", job_id, exc)
                self._fail_build(job, job_id, str(exc))

    def _fail_build(self, job: dict[str, Any], job_id: str, message: str) -> None:
        with self._lock:
            state = self._state_for(job_id)
            state.phase = VideoBuildPhase.FAILED
            state.error = message
            state.building = False
        try:
            self._unlink_built_video(job)
        except Exception:  # noqa: BLE001
            pass


def validate_video_build_duration(youtube_url: str) -> None:
    """Guardrail for on-demand video builds (defaults match source limits)."""
    validate_youtube_source_limits(youtube_url)


def inspect_video_duration_seconds(youtube_url: str) -> int | None:
    info = inspect_youtube_source(youtube_url.strip())
    duration = info.get("duration")
    if duration is None:
        return None
    try:
        return int(duration)
    except (TypeError, ValueError):
        return None


_video_build_manager: VideoBuildManager | None = None


def init_video_build_manager(work_dir: Path) -> VideoBuildManager:
    global _video_build_manager
    _video_build_manager = VideoBuildManager(work_dir)
    return _video_build_manager


def get_video_build_manager() -> VideoBuildManager | None:
    return _video_build_manager


def discard_video_build_for_job(job_id: str) -> None:
    mgr = get_video_build_manager()
    if mgr and job_id:
        mgr.discard_job(job_id)


def cleanup_stale_built_videos(work_dir: Path) -> int:
    mgr = get_video_build_manager()
    if mgr:
        return mgr.cleanup_expired_files()
    return 0
