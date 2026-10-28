from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path

from fastapi import HTTPException

from remastered.config import RemasteredSettings, resolve_settings
from remastered.pipeline import PipelineProgress
from remastered.web.cleanup import cleanup_old_jobs
from remastered.web.db import WEB_STAGES, JobStore
from remastered.web.job_media_paths import job_workspace_dir
from remastered.web.limits import validate_youtube_source_limits
from remastered.stereo_width import DEFAULT_STEREO_WIDTH_MODE, VALID_STEREO_WIDTH_MODES
from remastered.web.pipeline_web import download_source_audio, master_downloaded_audio
from remastered.web.processing_stage import (
    STAGE_DONE,
    STAGE_DOWNLOADING_REFERENCE,
    STAGE_DOWNLOADING_SOURCE,
    STAGE_FINALIZING,
    STAGE_MATCHING,
)
from remastered.web.reference_resolve import REFERENCE_DOWNLOAD_FAILED, resolve_reference_wav
from remastered.web.reference_youtube import is_youtube_reference_job
from remastered.web.stream_audio import transcode_wav_for_playback

logger = logging.getLogger(__name__)

ORPHAN_REQUEUE_MESSAGE = (
    "Job re-queued after the server restarted while it was running."
)


class JobWorker:
    def __init__(self, store: JobStore, settings: RemasteredSettings) -> None:
        self.store = store
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._last_cleanup = 0.0
        self._last_served_user_id: str | None = None

    def start(self) -> None:
        requeued = self.store.requeue_orphaned_running_jobs(ORPHAN_REQUEUE_MESSAGE)
        if requeued:
            logger.info("Requeued %d orphaned running job(s)", requeued)
        self._thread = threading.Thread(target=self._loop, name="remastered-jobs", daemon=True)
        self._thread.start()
        logger.info("Job worker thread started")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def enqueue(self, job_id: str) -> None:
        self._wake.set()

    def _maybe_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 3600:
            return
        self._last_cleanup = now
        try:
            cleanup_old_jobs(self.store, self.settings.work_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("정리 작업 실패: %s", exc)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._maybe_cleanup()
                job_id = self.store.next_pending_fair(self._last_served_user_id)
                if not job_id:
                    self._wake.wait(timeout=2.0)
                    self._wake.clear()
                    continue
                logger.info("Dequeuing job %s", job_id)
                self._run_job(job_id)
            except Exception:  # noqa: BLE001
                logger.exception("Job worker loop error")
                time.sleep(1.0)

    def _set_processing_stage(self, job_id: str, stage: str) -> None:
        self.store.update_job(job_id, processing_stage=stage)

    def _run_job(self, job_id: str) -> None:
        try:
            job = self.store.get_job(job_id)
            if not job or job["status"] != "pending":
                return
            user_id = (job.get("user_id") or "").strip()
            if not user_id:
                self.store.update_job(job_id, status="failed", error="Missing user_id")
                return

            self.store.update_job(
                job_id,
                status="running",
                stage="download",
                error=None,
                processing_stage=STAGE_DOWNLOADING_SOURCE,
            )
            self._last_served_user_id = user_id
            stage_state = dict(job["stage_state"])

            def log(msg: str) -> None:
                self.store.append_log(job_id, msg)

            def stage_start(stage: str) -> None:
                stage_state[stage] = "running"
                self.store.update_job(job_id, stage=stage, stage_state=stage_state)

            def stage_done(stage: str) -> None:
                stage_state[stage] = "done"
                self.store.update_job(job_id, stage_state=stage_state)

            progress = PipelineProgress(log=log, stage_start=stage_start, stage_done=stage_done)

            try:
                validate_youtube_source_limits(job["youtube_url"])
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                log(detail)
                self.store.update_job(
                    job_id,
                    status="failed",
                    error=detail,
                    stage_state=stage_state,
                    processing_stage=STAGE_DOWNLOADING_SOURCE,
                )
                return

            user_work = self.settings.work_dir / "users" / user_id
            job_workspace = job_workspace_dir(self.settings.work_dir, user_id, job_id)
            job_workspace.mkdir(parents=True, exist_ok=True)
            job_settings = self.settings.model_copy(update={"work_dir": job_workspace})

            def on_downloaded(title: str, video_id: str) -> None:
                self.store.update_job(
                    job_id,
                    title=title,
                    source_video_id=video_id,
                )

            try:
                dl = download_source_audio(
                    job["youtube_url"],
                    job_settings,
                    progress=progress,
                    on_downloaded=on_downloaded,
                )
            except Exception as exc:  # noqa: BLE001
                log(f"Error: {exc}")
                self.store.update_job(
                    job_id,
                    status="failed",
                    error=str(exc),
                    stage_state=stage_state,
                    processing_stage=STAGE_DOWNLOADING_SOURCE,
                )
                return

            fail_stage = (
                STAGE_DOWNLOADING_REFERENCE
                if is_youtube_reference_job(job)
                else STAGE_DOWNLOADING_SOURCE
            )
            if is_youtube_reference_job(job):
                self._set_processing_stage(job_id, STAGE_DOWNLOADING_REFERENCE)
            try:
                reference, ref_title = resolve_reference_wav(
                    job, self.settings, log=log
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                log(detail)
                self.store.update_job(
                    job_id,
                    status="failed",
                    error=detail,
                    stage_state=stage_state,
                    processing_stage=fail_stage,
                )
                return
            except RuntimeError as exc:
                message = str(exc).split(" (", 1)[0]
                if not message.startswith("Could not download"):
                    message = REFERENCE_DOWNLOAD_FAILED
                log(message)
                self.store.update_job(
                    job_id,
                    status="failed",
                    error=message,
                    stage_state=stage_state,
                    processing_stage=fail_stage,
                )
                return
            except FileNotFoundError as exc:
                log(str(exc))
                self.store.update_job(
                    job_id,
                    status="failed",
                    error=str(exc),
                    stage_state=stage_state,
                    processing_stage=fail_stage,
                )
                return
            if ref_title:
                self.store.update_job(job_id, reference_youtube_title=ref_title)

            stereo_width = job.get("stereo_width_mode") or DEFAULT_STEREO_WIDTH_MODE
            if stereo_width not in VALID_STEREO_WIDTH_MODES:
                stereo_width = DEFAULT_STEREO_WIDTH_MODE

            self._set_processing_stage(job_id, STAGE_MATCHING)
            try:
                result = master_downloaded_audio(
                    dl,
                    reference,
                    job_settings,
                    progress=progress,
                    stereo_width=stereo_width,
                )
            except Exception as exc:  # noqa: BLE001
                log(f"Error: {exc}")
                self.store.update_job(
                    job_id,
                    status="failed",
                    error=str(exc),
                    stage_state=stage_state,
                    processing_stage=STAGE_MATCHING,
                )
                return

            self._set_processing_stage(job_id, STAGE_FINALIZING)
            audio_wav = result.get("audio")
            mastered_wav = result.get("mastered")
            if isinstance(audio_wav, Path):
                try:
                    transcode_wav_for_playback(audio_wav)
                except Exception as exc:  # noqa: BLE001
                    log(f"Playback transcode failed (original): {exc}")
            if isinstance(mastered_wav, Path):
                try:
                    transcode_wav_for_playback(mastered_wav)
                except Exception as exc:  # noqa: BLE001
                    log(f"Playback transcode failed (mastered): {exc}")
            self.store.update_job(
                job_id,
                status="completed",
                stage="done",
                title=str(result.get("title") or job.get("title") or ""),
                source_video_id=str(result.get("video_id", "")),
                uploaded_video_id="",
                audio_path=str(result.get("audio", "")),
                mastered_path=str(result.get("mastered", "")),
                video_path="",
                stage_state={
                    **{s: "done" for s in WEB_STAGES},
                    "render": "skipped",
                    "upload": "skipped",
                },
                processing_stage=STAGE_DONE,
            )
            log("Mastering complete.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s failed", job_id)
            try:
                self.store.append_log(job_id, f"Error: {exc}")
                self.store.append_log(job_id, traceback.format_exc())
                self.store.update_job(
                    job_id,
                    status="failed",
                    error=str(exc),
                )
            except Exception:  # noqa: BLE001
                logger.exception("Could not persist failure for job %s", job_id)

    def refresh_settings(self) -> None:
        self.settings = resolve_settings()


def build_worker(store: JobStore) -> JobWorker:
    settings = resolve_settings()
    return JobWorker(store, settings)
