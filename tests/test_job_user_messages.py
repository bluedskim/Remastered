from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.db import JobStore
from remastered.web.job_user_messages import (
    MSG_TP_SAVE,
    classify_failure_message,
    prepare_job_logs,
    sanitize_log_message,
    user_facing_job_error,
)


def test_classify_exdev_legacy_error() -> None:
    raw = (
        "True-peak protection failed: [Errno 18] Invalid cross-device link: "
        "'/tmp/tmpp0d8k9p1.wav' -> '/work/users/u1/jobs/x/master/mastered_16bit.wav'"
    )
    msg, reason = classify_failure_message(raw, processing_stage="finalizing")
    assert msg == MSG_TP_SAVE
    assert reason == "true_peak_save_failed"
    assert "/work" not in msg
    assert "Errno" not in msg


def test_sanitize_traceback_log_line() -> None:
    raw = "Traceback (most recent call last):\n  File \"/work/foo.py\", line 1"
    assert "Traceback" not in sanitize_log_message(raw)
    assert "/work" not in sanitize_log_message(raw)


def test_user_facing_job_error_preserves_safe_validation() -> None:
    assert user_facing_job_error("Source is too long for this site.") == (
        "Source is too long for this site."
    )


def test_prepare_job_logs_redacts_historical_error_lines() -> None:
    logs = [
        {
            "message": "Error: [Errno 18] Invalid cross-device link: '/tmp/a' -> '/work/b'",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    out = prepare_job_logs(logs)
    assert "Errno" not in out[0]["message"]
    assert "/work" not in out[0]["message"]
    assert "/tmp" not in out[0]["message"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"test-audio")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "Test Track"},
    )
    from remastered.web.app import fastapi_application

    with TestClient(fastapi_application) as test_client:
        yield test_client


def test_failed_job_panel_hides_legacy_paths_and_errno(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "remastered.web.worker.JobWorker.enqueue",
        lambda self, job_id: None,
    )
    client.get("/")
    store = client.app.state.store
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=sanitizeTest",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].split("/")[-1]
    leaked = (
        "True-peak protection failed: [Errno 18] Invalid cross-device link: "
        "'/tmp/tmpp0d8k9p1.wav' -> '/work/users/anon/jobs/abc/master/mastered_16bit.wav'"
    )
    store.update_job(
        job_id,
        status="failed",
        error=leaked,
        processing_stage="finalizing",
    )
    store.append_log(job_id, f"Error: {leaked}")

    panel = client.get(f"/jobs/{job_id}/panel")
    assert panel.status_code in (200, 286)
    text = panel.text.lower()
    assert "/work/" not in panel.text
    assert "/tmp/" not in panel.text
    assert "errno" not in text
    assert "cross-device" not in text
    assert "true-peak" in text or "try again" in text
