from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.anon_visit import ANON_VISIT_COOKIE
from remastered.web.app import fastapi_application


@pytest.fixture
def anon_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
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
        lambda url: {"duration": 60, "filesize": 1000, "title": "Ephemeral Track"},
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
            "youtube_url": "https://www.youtube.com/watch?v=ephemeral1",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    job_id = resp.headers["location"].split("/")[-1]
    _wait_job_complete(client, job_id)
    return job_id


def test_anon_jobs_purged_when_visit_cookie_changes(anon_client: TestClient) -> None:
    job_id = _create_completed_job(anon_client)
    home = anon_client.get("/")
    assert job_id in home.text

    anon_client.cookies.set(ANON_VISIT_COOKIE, "new-browser-visit-id")
    home_after = anon_client.get("/")
    assert job_id not in home_after.text

    assert anon_client.get(f"/jobs/{job_id}").status_code == 404
    assert anon_client.get(f"/media/{job_id}/mastered").status_code == 404


def test_logged_in_jobs_survive_visit_cookie_change(
    anon_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import re

    job_id = _create_completed_job(anon_client)
    caplog.set_level("INFO")
    anon_client.post("/auth/send-verification", data={"login_id": "keep@example.com"})
    code = None
    for record in caplog.records:
        match = re.search(r"Email verification code for .+: (\d{6})", record.message)
        if match:
            code = match.group(1)
    assert code
    signup = anon_client.post(
        "/signup",
        data={
            "login_id": "keep@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303

    anon_client.cookies.set(ANON_VISIT_COOKIE, "another-visit-id")
    home = anon_client.get("/")
    assert job_id in home.text
    assert anon_client.get(f"/jobs/{job_id}").status_code == 200
    assert anon_client.get(f"/media/{job_id}/mastered").status_code == 200


def test_anon_same_visit_cookie_keeps_jobs(anon_client: TestClient) -> None:
    anon_client.cookies.set(ANON_VISIT_COOKIE, "stable-visit")
    job_id = _create_completed_job(anon_client)
    home = anon_client.get("/")
    assert job_id in home.text
    anon_client.cookies.set(ANON_VISIT_COOKIE, "stable-visit")
    assert job_id in anon_client.get("/").text
