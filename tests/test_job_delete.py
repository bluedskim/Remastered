from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.config import resolve_settings
from remastered.web.app import fastapi_application
from remastered.web.db import JobStore


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

    with TestClient(fastapi_application) as test_client:
        yield test_client


def _wait_job_complete(client: TestClient, job_id: str) -> None:
    for _ in range(40):
        panel = client.get(f"/jobs/{job_id}/panel")
        if panel.status_code == 286:
            return
        time.sleep(0.05)
    pytest.fail("job did not complete")


def _create_completed_job(client: TestClient) -> str:
    client.get("/")
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=deleteTest1",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].split("/")[-1]
    _wait_job_complete(client, job_id)
    return job_id


def _job_media_paths(work_dir: Path, job_id: str) -> list[Path]:
    store = JobStore(work_dir / "remastered_jobs.db")
    job = store.get_job(job_id)
    assert job is not None
    paths: list[Path] = []
    for key in ("audio_path", "mastered_path"):
        raw = job.get(key)
        if raw:
            paths.append(Path(raw))
    return paths


def test_owner_can_delete_job_and_files(client: TestClient, tmp_path: Path) -> None:
    job_id = _create_completed_job(client)
    work_dir = resolve_settings().work_dir
    media_paths = _job_media_paths(work_dir, job_id)
    assert all(p.is_file() for p in media_paths)

    home_before = client.get("/")
    assert job_id in home_before.text
    assert 'data-job-delete="' + job_id + '"' in home_before.text
    assert "job-card-thumb-column" in home_before.text
    assert home_before.text.index("job-card-thumb-column") < home_before.text.index(
        f'data-job-delete="{job_id}"'
    )

    delete = client.delete(f"/api/jobs/{job_id}")
    assert delete.status_code == 200
    assert delete.json() == {"ok": True}

    store = JobStore(work_dir / "remastered_jobs.db")
    assert store.get_job(job_id) is None
    assert store.get_logs(job_id) == []
    for path in media_paths:
        assert not path.is_file()

    home_after = client.get("/")
    assert job_id not in home_after.text
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert client.get(f"/media/{job_id}/mastered").status_code == 404


def test_non_owner_cannot_delete_job(client: TestClient, tmp_path: Path) -> None:
    job_id = _create_completed_job(client)
    other = TestClient(fastapi_application)
    other.get("/")

    denied = other.delete(f"/api/jobs/{job_id}")
    assert denied.status_code == 404

    work_dir = resolve_settings().work_dir
    assert JobStore(work_dir / "remastered_jobs.db").get_job(job_id) is not None


def test_signed_in_owner_can_delete(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    import re

    caplog.set_level("INFO")
    send = client.post("/auth/send-verification", data={"login_id": "deleter@example.com"})
    assert send.status_code == 200
    code = None
    for record in caplog.records:
        match = re.search(r"Email verification code for .+: (\d{6})", record.message)
        if match:
            code = match.group(1)
    assert code

    signup = client.post(
        "/signup",
        data={
            "login_id": "deleter@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303

    job_id = _create_completed_job(client)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_job_detail_page_has_delete_control(client: TestClient) -> None:
    job_id = _create_completed_job(client)
    detail = client.get(f"/jobs/{job_id}")
    assert detail.status_code == 200
    assert 'id="job-delete-btn"' in detail.text
    assert f'data-job-delete="{job_id}"' in detail.text
