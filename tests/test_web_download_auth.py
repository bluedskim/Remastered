from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application
from remastered.web.auth_jwt import AUTH_COOKIE_NAME
from remastered.web.stream_audio import playback_path_for_wav


@pytest.fixture
def dl_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
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

    def fake_web_pipeline(
        url: str,
        reference: Path,
        settings,
        progress=None,
        on_downloaded=None,
        stereo_width=None,
    ):
        if on_downloaded:
            on_downloaded("Claim Track", "vid")
        audio = settings.work_dir / "vid" / "audio.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"RIFF" + b"\x00" * 100)
        mastered = settings.work_dir / "vid" / "master" / "out.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        mastered.write_bytes(b"RIFF" + b"\x00" * 100)
        return {
            "audio": audio,
            "mastered": mastered,
            "video_id": "vid",
            "title": "Claim Track",
        }

    monkeypatch.setattr("remastered.web.worker.run_web_master_pipeline", fake_web_pipeline)
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 60, "filesize": 1000, "title": "Claim Track"},
    )

    with TestClient(fastapi_application) as test_client:
        yield test_client


def _verification_code(caplog: pytest.LogCaptureFixture) -> str:
    for record in caplog.records:
        match = re.search(r"Email verification code for .+: (\d{6})", record.message)
        if match:
            return match.group(1)
    raise AssertionError("verification code not found")


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
            "youtube_url": "https://www.youtube.com/watch?v=claim-dl",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    job_id = resp.headers["location"].split("/")[-1]
    _wait_job_complete(client, job_id)
    return job_id


def test_anon_mastered_download_redirects_to_signup(dl_client: TestClient) -> None:
    job_id = _create_completed_job(dl_client)
    resp = dl_client.get(
        f"/media/{job_id}/mastered?download=1",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/signup?next=%2Fjobs%2F{job_id}"


def test_anon_mastered_download_json_returns_401(dl_client: TestClient) -> None:
    job_id = _create_completed_job(dl_client)
    resp = dl_client.get(
        f"/media/{job_id}/mastered?download=1",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert resp.status_code == 401


def test_anon_streaming_still_works(dl_client: TestClient) -> None:
    job_id = _create_completed_job(dl_client)
    resp = dl_client.get(f"/media/{job_id}/mastered")
    assert resp.status_code == 200
    assert resp.headers.get("content-disposition", "").lower().startswith("inline")


def test_logged_in_owner_can_download_attachment(dl_client: TestClient, caplog) -> None:
    job_id = _create_completed_job(dl_client)
    caplog.set_level("INFO")
    dl_client.post("/auth/send-verification", data={"login_id": "owner@example.com"})
    code = _verification_code(caplog)
    signup = dl_client.post(
        "/signup",
        data={
            "login_id": "owner@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "nickname": "owner",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303
    assert AUTH_COOKIE_NAME in signup.cookies

    attach = dl_client.get(f"/media/{job_id}/mastered?download=1")
    assert attach.status_code == 200
    assert "attachment" in attach.headers.get("content-disposition", "").lower()


def test_signup_with_next_returns_to_job_and_allows_download(
    dl_client: TestClient,
    caplog,
) -> None:
    job_id = _create_completed_job(dl_client)
    caplog.set_level("INFO")
    dl_client.post("/auth/send-verification", data={"login_id": "claim@example.com"})
    code = _verification_code(caplog)
    signup = dl_client.post(
        "/signup",
        data={
            "login_id": "claim@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "nickname": "claimer",
            "verification_code": code,
            "next": f"/jobs/{job_id}",
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303
    assert signup.headers["location"] == f"/jobs/{job_id}"

    attach = dl_client.get(f"/media/{job_id}/mastered?download=1")
    assert attach.status_code == 200
    assert "attachment" in attach.headers.get("content-disposition", "").lower()


def test_login_ignores_unsafe_next_after_success(
    dl_client: TestClient,
    caplog,
) -> None:
    caplog.set_level("INFO")
    dl_client.post("/auth/send-verification", data={"login_id": "next@example.com"})
    code = _verification_code(caplog)
    dl_client.post(
        "/signup",
        data={
            "login_id": "next@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "nickname": "nextuser",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    dl_client.cookies.clear()
    dl_client.get("/")
    login = dl_client.post(
        "/login",
        data={
            "login_id": "next@example.com",
            "password": "password123",
            "next": "//evil.example",
        },
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/"
