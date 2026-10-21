from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from remastered.web import app as app_module
from remastered.web.app import app, require_signed_in


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("REMASTERED_TOKEN_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encryption_key: str) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"test-audio")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("REMASTERED_GOOGLE_CLIENT_SECRET", "test-secret")

    def fake_pipeline(
        url: str,
        reference: Path,
        settings,
        progress=None,
        **kwargs,
    ):
        prog = progress
        if prog:
            for stage in ("download", "master", "render", "upload"):
                if stage == "upload" and kwargs.get("skip_upload"):
                    continue
                prog.stage_start(stage)
                prog.stage_done(stage)
        audio = work / "users" / "u1" / "vid" / "audio.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"wav")
        mastered = work / "users" / "u1" / "vid" / "master" / "out.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        mastered.write_bytes(b"wav")
        video = work / "users" / "u1" / "vid" / "vid_remastered.mp4"
        video.write_bytes(b"mp4")
        result = {
            "audio": audio,
            "mastered": mastered,
            "video": video,
            "video_id": "vid",
        }
        if not kwargs.get("skip_upload"):
            result["uploaded_video_id"] = "UPLOADED1"
        return result

    monkeypatch.setattr("remastered.web.worker.run_pipeline_progress", fake_pipeline)
    monkeypatch.setattr(
        "remastered.web.worker.get_user_credentials",
        lambda store, uid: object(),
    )
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000},
    )

    with TestClient(app) as test_client:
        yield test_client


def _register_user(store, user_id: str = "u1") -> str:
    return store.upsert_user(
        google_sub=f"sub-{user_id}",
        email=f"{user_id}@example.com",
        name=user_id,
        picture=None,
        encrypted_token=b"x",
    )


def test_landing_anonymous(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Google로 로그인" in resp.text
    assert client.get("/dashboard", follow_redirects=False).status_code == 303


def test_index_and_create_job(client: TestClient) -> None:
    user_id = _register_user(client.app.state.store, "u1")
    app.dependency_overrides[require_signed_in] = lambda: user_id
    try:
        home = client.get("/dashboard")
        assert home.status_code == 200
        assert "새 작업" in home.text

        resp = client.post(
            "/jobs",
            data={
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "reference_name": "ref.wav",
                "title": "테스트 Remaster",
                "description": "웹 UI",
                "privacy": "private",
                "upload_enabled": "on",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        job_url = resp.headers["location"]

        for _ in range(40):
            panel = client.get(job_url + "/panel")
            if "status-completed" in panel.text:
                break
            time.sleep(0.05)
        else:
            pytest.fail("job did not complete")
    finally:
        app.dependency_overrides.clear()


def test_job_isolation(client: TestClient) -> None:
    store = client.app.state.store
    u1 = _register_user(store, "alice")
    u2 = _register_user(store, "bob")
    job_id = store.create_job(
        user_id=u1,
        youtube_url="https://youtu.be/x",
        reference_path="/references/ref.wav",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    app.dependency_overrides[require_signed_in] = lambda: u2
    try:
        assert client.get(f"/jobs/{job_id}").status_code == 404
        assert client.get(f"/media/{job_id}/mastered").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_daily_quota(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMASTERED_WEB_DAILY_JOB_QUOTA_PER_USER", "1")
    store = client.app.state.store
    user_id = _register_user(store, "quota")
    store.create_job(
        user_id=user_id,
        youtube_url="https://youtu.be/a",
        reference_path="/x",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    app.dependency_overrides[require_signed_in] = lambda: user_id
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 60, "filesize": 1000},
    )
    try:
        resp = client.post(
            "/jobs",
            data={
                "youtube_url": "https://www.youtube.com/watch?v=abc",
                "reference_name": "ref.wav",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 429
    finally:
        app.dependency_overrides.clear()
