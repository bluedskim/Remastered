from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from remastered.web.app import app, require_signed_in
from remastered.web.media import RETENTION_MESSAGE


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

    def fake_web_pipeline(url: str, reference: Path, settings, progress=None):
        prog = progress
        if prog:
            prog.stage_start("download")
            prog.stage_done("download")
            prog.stage_start("master")
            prog.stage_done("master")
        audio = settings.work_dir / "vid" / "audio.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"RIFF" + b"\x00" * 100)
        mastered = settings.work_dir / "vid" / "master" / "out.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        mastered.write_bytes(b"RIFF" + b"\x00" * 100)
        return {"audio": audio, "mastered": mastered, "video_id": "vid"}

    monkeypatch.setattr("remastered.web.worker.run_web_master_pipeline", fake_web_pipeline)
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


def test_oauth_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encryption_key: str
) -> None:
    monkeypatch.delenv("REMASTERED_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("REMASTERED_GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("REMASTERED_CLIENT_SECRETS", raising=False)
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(tmp_path / "refs"))
    (tmp_path / "refs").mkdir()

    with TestClient(app) as test_client:
        home = test_client.get("/")
        assert home.status_code == 200
        assert "Google 로그인이 아직 설정되지 않았습니다" in home.text
        assert "Google로 로그인" not in home.text

        auth = test_client.get("/auth/google")
        assert auth.status_code == 200
        assert "Google 로그인이 아직 설정되지 않았습니다" in auth.text

        dash = test_client.get("/dashboard", follow_redirects=False)
        assert dash.status_code == 303
        assert dash.headers["location"] == "/auth/unavailable"


def test_index_and_create_job(client: TestClient) -> None:
    user_id = _register_user(client.app.state.store, "u1")
    app.dependency_overrides[require_signed_in] = lambda: user_id
    try:
        home = client.get("/dashboard")
        assert home.status_code == 200
        assert "새 작업" in home.text
        assert "YouTube에 업로드" not in home.text

        resp = client.post(
            "/jobs",
            data={
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "reference_name": "ref.wav",
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

        media = client.get(job_url.replace("/jobs/", "/media/") + "/mastered")
        assert media.status_code == 200
        ranged = client.get(
            job_url.replace("/jobs/", "/media/") + "/mastered",
            headers={"Range": "bytes=0-3"},
        )
        assert ranged.status_code == 206
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


def test_missing_media_retention_message(client: TestClient, tmp_path: Path) -> None:
    store = client.app.state.store
    user_id = _register_user(store, "retention")
    work = tmp_path / "work"
    job_id = store.create_job(
        user_id=user_id,
        youtube_url="https://youtu.be/x",
        reference_path="/references/ref.wav",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    store.update_job(
        job_id,
        status="completed",
        audio_path=str(work / "users" / user_id / "missing.wav"),
        mastered_path=str(work / "users" / user_id / "missing2.wav"),
    )
    app.dependency_overrides[require_signed_in] = lambda: user_id
    try:
        panel = client.get(f"/jobs/{job_id}/panel")
        assert RETENTION_MESSAGE.split("。")[0] in panel.text or "보관 기간" in panel.text
        resp = client.get(f"/media/{job_id}/mastered")
        assert resp.status_code == 404
        assert RETENTION_MESSAGE in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()
