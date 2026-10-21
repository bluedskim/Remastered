from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path

from remastered.config import RemasteredSettings, resolve_settings
from remastered.pipeline import PipelineProgress, run_pipeline_progress
from remastered.web.db import STAGES, JobStore

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, store: JobStore, settings: RemasteredSettings) -> None:
        self.store = store
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

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

    def _loop(self) -> None:
        while not self._stop.is_set():
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
            reference = Path(job["reference_path"])
            if not reference.is_file():
                raise FileNotFoundError(f"레퍼런스 파일 없음: {reference}")

            result = run_pipeline_progress(
                job["youtube_url"],
                reference,
                self.settings,
                progress,
                title=job["title"],
                description=job["description"],
                privacy=job["privacy"],
                skip_upload=not job["upload_enabled"],
            )
            self.store.update_job(
                job_id,
                status="completed",
                stage="done",
                source_video_id=str(result.get("video_id", "")),
                uploaded_video_id=str(result.get("uploaded_video_id", "") or ""),
                audio_path=str(result.get("audio", "")),
                mastered_path=str(result.get("mastered", "")),
                video_path=str(result.get("video", "")),
                stage_state={s: ("skipped" if s == "upload" and not job["upload_enabled"] else "done") for s in STAGES},
            )
            log("작업이 완료되었습니다.")
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
