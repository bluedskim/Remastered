from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from remastered.download import DownloadResult
from remastered.web.db import JobStore
from remastered.web.worker import JobWorker
from remastered.config import resolve_settings


def _install_common_mocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
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
        mastered = settings.work_dir / "vid12345678" / "master" / "mastered_16bit.wav"
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
    monkeypatch.setattr(
        "remastered.web.worker.validate_youtube_source_limits",
        lambda url: None,
    )
    monkeypatch.setattr(
        "remastered.web.worker.transcode_wav_for_playback",
        lambda p: p.with_suffix(".m4a"),
    )
    return work, ref


def test_worker_skips_tp_when_job_opt_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work, ref = _install_common_mocks(tmp_path, monkeypatch)
    copy_calls: list[Path] = []

    def track_copy(mastered: Path) -> Path:
        copy_calls.append(mastered)
        raw = mastered.with_name("mastered_matchering_16bit.wav")
        shutil.copy2(mastered, raw)
        return raw

    monkeypatch.setattr(
        "remastered.web.worker.copy_matchering_to_raw",
        track_copy,
    )

    def boom(*_a, **_k):
        raise AssertionError("apply_final_true_peak_protection should not run when opted out")

    monkeypatch.setattr(
        "remastered.web.worker.apply_final_true_peak_protection",
        boom,
    )

    store = JobStore(work / "jobs.db")
    job_id = store.create_job(
        user_id="anon-tp-off",
        youtube_url="https://www.youtube.com/watch?v=tpOff1",
        reference_path=str(ref),
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
        final_tp_protection=False,
    )
    worker = JobWorker(store, resolve_settings())
    worker.start()
    try:
        for _ in range(80):
            job = store.get_job(job_id)
            assert job is not None
            if job["status"] == "completed":
                break
            time.sleep(0.05)
        else:
            pytest.fail("job did not complete")
        assert job["final_tp_protection"] is False
        assert copy_calls == []
    finally:
        worker.stop()


def test_worker_runs_tp_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work, ref = _install_common_mocks(tmp_path, monkeypatch)
    applied: list[tuple[Path, Path]] = []

    def fake_copy(mastered: Path) -> Path:
        raw = mastered.with_name("mastered_matchering_16bit.wav")
        shutil.copy2(mastered, raw)
        return raw

    def fake_apply(raw: Path, out: Path, **kwargs):
        applied.append((raw, out))
        shutil.copy2(raw, out)
        return (0.0, -6.0)

    monkeypatch.setattr("remastered.web.worker.copy_matchering_to_raw", fake_copy)
    monkeypatch.setattr(
        "remastered.web.worker.apply_final_true_peak_protection",
        fake_apply,
    )

    store = JobStore(work / "jobs.db")
    job_id = store.create_job(
        user_id="anon-tp-on",
        youtube_url="https://www.youtube.com/watch?v=tpOn1",
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
            time.sleep(0.05)
        else:
            pytest.fail("job did not complete")
        assert job.get("final_tp_protection", True) is True
        assert len(applied) == 1
    finally:
        worker.stop()


def test_worker_fails_job_when_tp_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work, ref = _install_common_mocks(tmp_path, monkeypatch)

    def fake_copy(mastered: Path) -> Path:
        raw = mastered.with_name("mastered_matchering_16bit.wav")
        shutil.copy2(mastered, raw)
        return raw

    def fake_apply(_raw: Path, _out: Path, **kwargs):
        raise RuntimeError("verification exhausted")

    monkeypatch.setattr("remastered.web.worker.copy_matchering_to_raw", fake_copy)
    monkeypatch.setattr(
        "remastered.web.worker.apply_final_true_peak_protection",
        fake_apply,
    )

    store = JobStore(work / "jobs.db")
    job_id = store.create_job(
        user_id="anon-tp-fail",
        youtube_url="https://www.youtube.com/watch?v=tpFail1",
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
            if job["status"] == "failed":
                break
            time.sleep(0.05)
        else:
            pytest.fail("job did not fail")
        assert "verification exhausted" in (job.get("error") or "")
    finally:
        worker.stop()
