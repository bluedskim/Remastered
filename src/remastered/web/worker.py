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
from remastered.web.limits import validate_youtube_source_limits
from remastered.web.pipeline_web import run_web_master_pipeline
from remastered.web.stream_audio import transcode_wav_for_playback

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, store: JobStore, settings: RemasteredSettings) -> None:
        self.store = store
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._last_cleanup = 0.0

    def start(self) -> None:
        self.store.reset_interrupted()
        self._thread = threading.Thread(target=self._loop, name="remastered-jobs", daemon=True)
        self._thread.start()

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
            self._maybe_cleanup()
            job_id = self.store.next_pending()
            if not job_id:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue
            self._run_job(job_id)

    def _run_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if not job or job["status"] != "pending":
            return
        user_id = job.get("user_id")
        if not user_id:
            self.store.update_job(job_id, status="failed", error="user_id 없음")
            return

        self.store.update_job(job_id, status="running", stage="download", error=None)
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
                )
                return

            reference = Path(job["reference_path"])
            if not reference.is_file():
                raise FileNotFoundError(f"레퍼런스 파일 없음: {reference}")

            user_work = self.settings.work_dir / "users" / user_id
            user_work.mkdir(parents=True, exist_ok=True)
            job_settings = self.settings.model_copy(update={"work_dir": user_work})

            def on_downloaded(title: str, video_id: str) -> None:
                self.store.update_job(
                    job_id,
                    title=title,
                    source_video_id=video_id,
                )

            result = run_web_master_pipeline(
                job["youtube_url"],
                reference,
                job_settings,
                progress,
                on_downloaded=on_downloaded,
            )
            audio_wav = result.get("audio")
            mastered_wav = result.get("mastered")
            if isinstance(audio_wav, Path):
                try:
                    transcode_wav_for_playback(audio_wav)
                except Exception as exc:  # noqa: BLE001
                    log(f"재생용 오디오 변환 실패(원본): {exc}")
            if isinstance(mastered_wav, Path):
                try:
                    transcode_wav_for_playback(mastered_wav)
                except Exception as exc:  # noqa: BLE001
                    log(f"재생용 오디오 변환 실패(마스터): {exc}")
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
            )
            log("마스터링이 완료되었습니다.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job %s failed", job_id)
            log(f"오류: {exc}")
            log(traceback.format_exc())
            self.store.update_job(
                job_id,
                status="failed",
                error=str(exc),
                stage_state=stage_state,
            )

    def refresh_settings(self) -> None:
        self.settings = resolve_settings()


def build_worker(store: JobStore) -> JobWorker:
    settings = resolve_settings()
    return JobWorker(store, settings)
