from __future__ import annotations

import time
from pathlib import Path

import pytest

from remastered.config import resolve_settings
from remastered.web.db import LEGACY_MIGRATION_USER_ID, JobStore
from remastered.web.worker import ORPHAN_REQUEUE_MESSAGE, JobWorker


def test_requeue_orphaned_running_jobs(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    job_id = store.create_job(
        user_id="u1",
        youtube_url="https://youtu.be/x",
        reference_path="/references/ref.wav",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    store.update_job(job_id, status="running")
    count = store.requeue_orphaned_running_jobs(ORPHAN_REQUEUE_MESSAGE)
    assert count == 1
    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "pending"
    logs = store.get_logs(job_id)
    assert any(ORPHAN_REQUEUE_MESSAGE in line["message"] for line in logs)


def test_next_pending_skips_invalid_user_id(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    bad = store.create_job(
        user_id="",
        youtube_url="https://youtu.be/bad",
        reference_path="/references/ref.wav",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
        client_ip=None,
    )
    good = store.create_job(
        user_id="anon-good",
        youtube_url="https://youtu.be/good",
        reference_path="/references/ref.wav",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
        client_ip="203.0.113.1",
    )
    assert store.next_pending_fair(None) == good
    assert store.queue_position(bad) >= 1


def test_next_pending_legacy_mixed_db(tmp_path: Path) -> None:
    """Realistic DB: legacy owner + NULL client_ip still dequeues."""
    store = JobStore(tmp_path / "jobs.db")
    legacy = store.create_job(
        user_id=LEGACY_MIGRATION_USER_ID,
        youtube_url="https://youtu.be/legacy",
        reference_path="/references/ref.wav",
        title="Legacy track",
        description="",
        privacy="private",
        upload_enabled=False,
        client_ip=None,
    )
    modern = store.create_job(
        user_id="anon-modern",
        youtube_url="https://youtu.be/modern",
        reference_path="/references/ref.wav",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
        client_ip="198.51.100.2",
    )
    first = store.next_pending_fair(None)
    assert first in {legacy, modern}
    assert store.count_pending_jobs() == 2


def test_worker_starts_and_picks_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    store = JobStore(work / "remastered_jobs.db")
    job_id = store.create_job(
        user_id="anon-worker",
        youtube_url="https://www.youtube.com/watch?v=worker-test",
        reference_path=str(refs / "ref.wav"),
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )

    from tests.worker_patches import install_fake_worker_pipeline

    monkeypatch.setattr(
        "remastered.web.worker.validate_youtube_source_limits",
        lambda url: None,
    )
    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    monkeypatch.setattr(
        "remastered.web.worker.transcode_wav_for_playback",
        lambda p: p.with_suffix(".m4a"),
    )

    settings = resolve_settings()
    worker = JobWorker(store, settings)
    worker.start()
    try:
        for _ in range(50):
            job = store.get_job(job_id)
            assert job is not None
            if job["status"] in ("completed", "failed", "running"):
                break
            time.sleep(0.05)
        else:
            pytest.fail("worker did not pick up job")
        assert job["status"] in ("completed", "running")
    finally:
        worker.stop()
