from __future__ import annotations

import time
from pathlib import Path

import pytest

from remastered.download import DownloadResult
from remastered.web.db import JobStore
from remastered.web.processing_stage import STAGE_DONE, STAGE_DOWNLOADING_SOURCE
from remastered.web.worker import JobWorker
from remastered.config import resolve_settings


def test_worker_updates_processing_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    ref = refs / "ref.wav"
    ref.write_bytes(b"RIFF" + b"\x00" * 16)
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))

    def fake_download(url, settings, progress=None, on_downloaded=None):
        audio = settings.work_dir / "vid12345678" / "audio.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"RIFF" + b"\x00" * 32)
        if on_downloaded:
            on_downloaded("Track", "vid12345678")
        return DownloadResult(
            video_id="vid12345678",
            title="Track",
            audio_path=audio,
            thumbnail_path=None,
            source_url=url,
        )

    def fake_master(dl, reference, settings, progress=None, stereo_width=None):
        mastered = settings.work_dir / "vid12345678" / "master" / "out.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        mastered.write_bytes(b"RIFF" + b"\x00" * 32)
        return {
            "audio": dl.audio_path,
            "mastered": mastered,
            "video_id": dl.video_id,
            "title": dl.title,
        }

    monkeypatch.setattr("remastered.web.worker.download_source_audio", fake_download)
    monkeypatch.setattr(
        "remastered.web.worker.resolve_reference_wav",
        lambda job, settings, log=None: (ref, None),
    )
    monkeypatch.setattr("remastered.web.worker.master_downloaded_audio", fake_master)
    monkeypatch.setattr("remastered.web.worker.validate_youtube_source_limits", lambda url: None)
    monkeypatch.setattr(
        "remastered.web.worker.transcode_wav_for_playback",
        lambda p: p.with_suffix(".m4a"),
    )

    store = JobStore(work / "jobs.db")
    job_id = store.create_job(
        user_id="anon-stage",
        youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        reference_path=str(ref),
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    worker = JobWorker(store, resolve_settings())
    worker.start()
    try:
        for _ in range(80):
            job = store.get_job(job_id)
            assert job is not None
            if job["status"] == "completed":
                break
            if job["status"] == "running" and job.get("processing_stage"):
                assert job["processing_stage"] in {
                    STAGE_DOWNLOADING_SOURCE,
                    "downloading_reference",
                    "matching",
                    "finalizing",
                    STAGE_DONE,
                }
            time.sleep(0.05)
        else:
            pytest.fail("job did not complete")
        assert job["processing_stage"] == STAGE_DONE
    finally:
        worker.stop()
