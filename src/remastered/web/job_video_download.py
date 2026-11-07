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

from remastered.config import resolve_settings
from remastered.web.download_filenames import video_remastered_download_filename
from remastered.web.job_media_paths import job_workspace_dir
from remastered.web.job_storage import job_media_storage_owner_id
from remastered.web.media import media_exists
from remastered.web.job_user_messages import user_facing_optional_error
from remastered.web.settings import WebSettings
from remastered.web.youtube_thumb_cache import (
    ensure_thumb_cached,
    thumb_cache_path,
)
from remastered.youtube_url import extract_youtube_video_id

logger = logging.getLogger(__name__)

BUILT_VIDEO_FILENAME = "remastered_download.mp4"
VIDEO_BUILD_TMP_DIRNAME = "_video_build_tmp"
FALLBACK_STILL_FILENAME = "_video_still.png"
OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
OUTPUT_FPS = 30
VIDEO_CRF = 23
AAC_BITRATE = "352k"

VIDEO_SCALE_PAD_FILTER = (
    f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
    "format=yuv420p"
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


def resolve_job_thumbnail_path(job: dict[str, Any], work_dir: Path) -> Path | None:
    """Return cached YouTube thumbnail path when available."""
    video_id = extract_youtube_video_id((job.get("youtube_url") or "").strip())
    if not video_id:
        source_id = (job.get("source_video_id") or "").strip()
        if source_id and len(source_id) == 11:
            video_id = source_id
    if not video_id:
        return None
    path = thumb_cache_path(work_dir, video_id)
    if path.is_file() and path.stat().st_size > 0:
        return path
    if ensure_thumb_cached(work_dir, video_id) and path.is_file():
        return path
    return None


def _escape_drawtext(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def generate_fallback_still_image(
    output_path: Path,
    *,
    title: str | None = None,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    """Dark 16:9 frame; optional centered title via ffmpeg drawtext."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    safe_title = (title or "").strip()
    if safe_title:
        text = _escape_drawtext(safe_title[:120])
        vf = (
            f"drawtext=text='{text}':fontcolor=white@0.85:fontsize=42:"
            "x=(w-text_w)/2:y=(h-text_h)/2"
        )
        cmd = [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x14101f:s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:d=1",
            "-vf",
            vf,
            "-frames:v",
            "1",
            str(output_path),
        ]
    else:
        cmd = [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x14101f:s={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}:d=1",
            "-frames:v",
            "1",
            str(output_path),
        ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg still frame failed (code {proc.returncode}): {proc.stderr[-2000:]}"
        )
    return output_path


def prepare_still_image_for_job(
    job: dict[str, Any],
    work_dir: Path,
    tmp_dir: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    thumb = resolve_job_thumbnail_path(job, work_dir)
    if thumb and thumb.is_file():
        return thumb
    title = (job.get("title") or "").strip() or None
    out = tmp_dir / FALLBACK_STILL_FILENAME
    return generate_fallback_still_image(out, title=title, ffmpeg_path=ffmpeg_path)


def build_still_image_video_with_audio(
    still_image: Path,
    mastered_wav: Path,
    output_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    """Encode YouTube-friendly MP4: looped still + full mastered audio."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path,
        "-y",
        "-loop",
        "1",
        "-i",
        str(still_image),
        "-i",
        str(mastered_wav),
        "-vf",
        VIDEO_SCALE_PAD_FILTER,
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-tune",
        "stillimage",
        "-crf",
        str(VIDEO_CRF),
        "-r",
        str(OUTPUT_FPS),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        AAC_BITRATE,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg video build failed (code {proc.returncode}): {proc.stderr[-2000:]}"
        )
    return output_path


def run_video_build(
    job: dict[str, Any],
    *,
    work_dir: Path | None = None,
    settings: WebSettings | None = None,
) -> Path:
    """Blocking build: still image (YouTube thumb or fallback) + mastered audio."""
    ws = settings or WebSettings()
    work = work_dir or resolve_settings().work_dir
    mastered = Path(job["mastered_path"])
    if not media_exists(str(mastered), job, work):
        raise RuntimeError("Remastered audio is unavailable for this job.")

    remastered_settings = resolve_settings()
    ffmpeg_path = remastered_settings.ffmpeg_path

    output_path = built_video_path(job, work)
    tmp_dir = _video_build_tmp_dir(output_path)
    _cleanup_tmp_dir(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        still = prepare_still_image_for_job(
            job, work, tmp_dir, ffmpeg_path=ffmpeg_path
        )
        if output_path.is_file():
            output_path.unlink(missing_ok=True)
        build_still_image_video_with_audio(
            still,
            mastered.resolve(),
            output_path,
            ffmpeg_path=ffmpeg_path,
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
                "error": user_facing_optional_error(state.error),
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
