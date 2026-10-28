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


def test_delete_claimed_job_with_legacy_anon_media_paths(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    import re

    job_id = _create_completed_job(client)
    work_dir = resolve_settings().work_dir
    store = JobStore(work_dir / "remastered_jobs.db")
    before = store.get_job(job_id)
    assert before is not None
    anon_id = before["user_id"]
    assert anon_id.startswith("anon-")
    media_path = before.get("mastered_path") or ""
    assert f"/users/{anon_id}/" in media_path.replace("\\", "/")

    caplog.set_level("INFO")
    client.post("/auth/send-verification", data={"login_id": "legacy-path@example.com"})
    code = None
    for record in caplog.records:
        match = re.search(r"Email verification code for .+: (\d{6})", record.message)
        if match:
            code = match.group(1)
    assert code
    signup = client.post(
        "/signup",
        data={
            "login_id": "legacy-path@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303

    claimed = store.get_job(job_id)
    assert claimed is not None
    assert claimed["user_id"].startswith("user-")
    assert f"/users/{anon_id}/" in (claimed.get("mastered_path") or "").replace("\\", "/")

    assert client.post(f"/api/jobs/{job_id}/delete").status_code == 200
    assert store.get_job(job_id) is None
    assert store.get_logs(job_id) == []
    assert client.get(f"/jobs/{job_id}").status_code == 404
    home = client.get("/")
    assert job_id not in home.text


def test_delete_survives_media_cleanup_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _create_completed_job(client)
    work_dir = resolve_settings().work_dir
    store = JobStore(work_dir / "remastered_jobs.db")

    def boom(job: dict, work_dir: Path) -> None:
        raise OSError("simulated media cleanup failure")

    monkeypatch.setattr(
        "remastered.web.cleanup.delete_job_media_files",
        boom,
    )

    resp = client.post(f"/api/jobs/{job_id}/delete")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert store.get_job(job_id) is None
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_delete_still_available_via_http_delete(client: TestClient) -> None:
    job_id = _create_completed_job(client)
    work_dir = resolve_settings().work_dir
    store = JobStore(work_dir / "remastered_jobs.db")
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert store.get_job(job_id) is None


def test_job_detail_page_has_delete_control(client: TestClient) -> None:
    job_id = _create_completed_job(client)
    detail = client.get(f"/jobs/{job_id}")
    assert detail.status_code == 200
    assert 'id="job-delete-btn"' in detail.text
    assert f'data-job-delete="{job_id}"' in detail.text


def test_get_job_for_access_ignores_user_id_whitespace(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    store = JobStore(work / "jobs.db")
    anon = "anon-testowner"
    job_id = store.create_job(
        user_id=anon,
        youtube_url="https://www.youtube.com/watch?v=wsTest1",
        reference_path="/refs/a.wav",
        title="T",
        description="",
        privacy="private",
        upload_enabled=False,
    )
    with store._conn() as conn:
        conn.execute(
            "UPDATE jobs SET user_id = ? WHERE id = ?",
            (anon + "  ", job_id),
        )
    row = store.get_job_for_access(
        job_id,
        account_id=None,
        anon_id=anon,
    )
    assert row is not None
    assert row["id"] == job_id
