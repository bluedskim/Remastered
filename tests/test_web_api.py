from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import app as asgi_app, fastapi_application
from remastered.web.limits import JobCreateRateLimiter
from remastered.web.media import RETENTION_MESSAGE
from remastered.web.stream_audio import playback_path_for_wav


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
    monkeypatch.delenv("REMASTERED_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("REMASTERED_GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("REMASTERED_TOKEN_ENCRYPTION_KEY", raising=False)

    def fake_web_pipeline(
        url: str, reference: Path, settings, progress=None, on_downloaded=None
    ):
        prog = progress
        if prog:
            prog.stage_start("download")
            prog.stage_done("download")
            prog.stage_start("master")
            prog.stage_done("master")
        if on_downloaded:
            on_downloaded("Test Track", "vid")
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
            "title": "Test Track",
        }

    monkeypatch.setattr("remastered.web.worker.run_web_master_pipeline", fake_web_pipeline)

    def fake_transcode(wav: Path) -> Path:
        out = playback_path_for_wav(wav)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 256)
        return out

    monkeypatch.setattr("remastered.web.worker.transcode_wav_for_playback", fake_transcode)
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "Test Track"},
    )

    with TestClient(fastapi_application) as test_client:
        yield test_client


def test_home_without_google_env(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "새 작업" in resp.text
    assert "Google" not in resp.text
    assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/"


def test_index_and_create_job(client: TestClient) -> None:
    home = client.get("/")
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
        if panel.status_code == 286:
            break
        time.sleep(0.05)
    else:
        pytest.fail("job did not complete")

    media = client.get(job_url.replace("/jobs/", "/media/") + "/mastered")
    assert media.status_code == 200
    assert media.headers.get("content-disposition", "").lower().startswith("inline")
    ranged = client.get(
        job_url.replace("/jobs/", "/media/") + "/mastered",
        headers={"Range": "bytes=0-3"},
    )
    assert ranged.status_code == 206

    job_id = job_url.split("/")[-1]
    panel = client.get(f"/jobs/{job_id}/panel")
    assert panel.status_code == 286
    detail = client.get(job_url)
    assert "every 2s" not in detail.text
    assert 'id="job-players"' in detail.text
    assert "<audio" in detail.text


def test_job_isolation(client: TestClient) -> None:
    client.get("/")
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=abc",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].split("/")[-1]
    client.cookies.clear()
    client.get("/")
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert client.get(f"/media/{job_id}/mastered").status_code == 404


def test_daily_quota_per_owner(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMASTERED_WEB_DAILY_JOB_QUOTA_PER_USER", "1")
    client.get("/")
    first = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=one",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    assert first.status_code == 303
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 60, "filesize": 1000},
    )
    second = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=two",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    assert second.status_code == 429


def test_daily_quota_per_ip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"x")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_WEB_DAILY_JOB_QUOTA_PER_USER", "1")
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 60, "filesize": 1000},
    )
    store_path = work / "remastered_jobs.db"
    from remastered.web.db import JobStore

    store = JobStore(store_path)
    store.create_job(
        user_id="anon-other",
        youtube_url="https://youtu.be/a",
        reference_path="/x",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
        client_ip="testclient",
    )

    with TestClient(fastapi_application) as fresh_browser:
        fresh_browser.get("/")
        resp = fresh_browser.post(
            "/jobs",
            data={
                "youtube_url": "https://www.youtube.com/watch?v=abc",
                "reference_name": "ref.wav",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 429


def test_missing_media_retention_message(client: TestClient) -> None:
    client.get("/")
    store = client.app.state.store
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=ret",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    job_id = resp.headers["location"].split("/")[-1]
    for _ in range(40):
        job = store.get_job(job_id)
        if job and job["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("job did not complete")
    assert job is not None
    owner_id = job["user_id"]
    work = client.app.state.store.db_path.parent
    store.update_job(
        job_id,
        status="completed",
        audio_path=str(work / "users" / owner_id / "missing.wav"),
        mastered_path=str(work / "users" / owner_id / "missing2.wav"),
    )
    panel = client.get(f"/jobs/{job_id}/panel")
    assert RETENTION_MESSAGE.split("。")[0] in panel.text or "보관 기간" in panel.text
    media_resp = client.get(f"/media/{job_id}/mastered")
    assert media_resp.status_code == 404
    assert RETENTION_MESSAGE in media_resp.json()["detail"]


def test_media_download_disposition(client: TestClient) -> None:
    client.get("/")
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=dl",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    job_id = resp.headers["location"].split("/")[-1]
    for _ in range(40):
        panel = client.get(f"/jobs/{job_id}/panel")
        if panel.status_code == 286:
            break
        time.sleep(0.05)
    else:
        pytest.fail("job did not complete")

    inline = client.get(f"/media/{job_id}/mastered")
    assert inline.headers.get("content-disposition", "").lower().startswith("inline")
    attach = client.get(f"/media/{job_id}/mastered?download=1")
    assert "attachment" in attach.headers.get("content-disposition", "").lower()


def test_job_title_on_dashboard_and_detail(client: TestClient) -> None:
    client.get("/")
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=title-test",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    job_id = resp.headers["location"].split("/")[-1]
    for _ in range(40):
        job = client.app.state.store.get_job(job_id)
        if job and job.get("title"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("title not set")

    dash = client.get("/")
    assert "Test Track" in dash.text
    detail = client.get(f"/jobs/{job_id}")
    assert "Test Track" in detail.text


def test_duplicate_submission_redirects_to_same_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "30")
    client.app.state.rate_limiter = JobCreateRateLimiter(30.0)

    client.get("/")
    payload = {
        "youtube_url": "https://www.youtube.com/watch?v=dup-test",
        "reference_name": "ref.wav",
    }
    first = client.post("/jobs", data=payload, follow_redirects=False)
    second = client.post("/jobs", data=payload, follow_redirects=False)
    assert first.status_code == 303
    assert second.status_code == 303
    assert first.headers["location"] == second.headers["location"]


def test_rate_limit_still_applies_to_different_jobs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "30")
    client.app.state.rate_limiter = JobCreateRateLimiter(30.0)
    client.get("/")
    first = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=rate-a",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    assert first.status_code == 303
    second = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=rate-b",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    assert second.status_code == 429
    assert "요청이 너무 잦습니다" in second.text
