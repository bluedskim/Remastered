from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

from remastered.web.app import PACKAGE_DIR, fastapi_application
from remastered.web.db import JobStore
from remastered.web.download_filenames import video_remastered_download_filename
from remastered.web.job_media_paths import job_workspace_dir
from remastered.web.job_video_download import (
    BUILT_VIDEO_FILENAME,
    VideoBuildManager,
    VideoBuildPhase,
    built_video_path,
    init_video_build_manager,
    job_is_youtube_sourced,
    job_video_download_eligible,
    run_video_build,
)


@pytest.fixture
def jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PACKAGE_DIR / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )


@pytest.fixture
def video_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"test-audio")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_WEB_MAX_CONCURRENT_VIDEO_BUILDS", "1")
    monkeypatch.setenv("REMASTERED_WEB_VIDEO_DOWNLOAD_CACHE_TTL_SECONDS", "3600")
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 60, "filesize": 1000, "title": "Video Track"},
    )

    build_started = threading.Event()

    def fake_run_video_build(job, *, work_dir=None, settings=None):
        build_started.set()
        root = work_dir or work
        out = built_video_path(job, root)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake-mp4")
        return out

    monkeypatch.setattr(
        "remastered.web.job_video_download.run_video_build",
        fake_run_video_build,
    )

    with TestClient(fastapi_application) as test_client:
        test_client.build_started = build_started  # type: ignore[attr-defined]
        test_client.work_dir = work  # type: ignore[attr-defined]
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


def _signup(client: TestClient, caplog: pytest.LogCaptureFixture, email: str = "video-dl@example.com") -> None:
    caplog.set_level("INFO")
    client.post("/auth/send-verification", data={"login_id": email})
    code = _verification_code(caplog)
    resp = client.post(
        "/signup",
        data={
            "login_id": email,
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303


def _create_completed_youtube_job(client: TestClient, video_id: str = "abc12345678") -> str:
    client.get("/")
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    job_id = resp.headers["location"].split("/")[-1]
    _wait_job_complete(client, job_id)
    return job_id


def test_video_button_shown_for_completed_youtube_job(jinja_env: Environment) -> None:
    html = jinja_env.get_template("partials/job_players.html").render(
        job={"id": "job-yt", "youtube_url": "https://www.youtube.com/watch?v=abc123"},
        audio_ok=True,
        mastered_ok=True,
        ab_compare_ok=True,
        video_download_show=True,
        video_download_filename="My Song (Remastered).mp4",
        auth_user={"email_display": "u@example.com"},
        mastered_download_filename="My Song (Remastered).wav",
        rights_notice_short="Rights",
    )
    assert "Download video" in html
    assert "data-video-download" in html


def test_video_button_hidden_without_youtube_flag(jinja_env: Environment) -> None:
    html = jinja_env.get_template("partials/job_players.html").render(
        job={"id": "job-up"},
        audio_ok=True,
        mastered_ok=True,
        ab_compare_ok=True,
        video_download_show=False,
        auth_user={"email_display": "u@example.com"},
        mastered_download_filename="t.wav",
        rights_notice_short="Rights",
    )
    assert "Download video" not in html


def test_job_is_youtube_sourced() -> None:
    assert job_is_youtube_sourced({"youtube_url": "https://youtu.be/abc12345678"})
    assert not job_is_youtube_sourced({"youtube_url": ""})
    assert not job_is_youtube_sourced({"youtube_url": "https://example.com/audio.wav"})


def test_video_remastered_filename() -> None:
    job = {"title": "Spring Day", "youtube_url": "https://youtu.be/dQw4w9WgXcQ"}
    assert video_remastered_download_filename(job) == "Spring Day (Remastered).mp4"


def test_anon_cannot_start_video_build(video_client: TestClient) -> None:
    job_id = _create_completed_youtube_job(video_client)
    resp = video_client.post(f"/api/jobs/{job_id}/video-download")
    assert resp.status_code == 401


def test_signed_in_video_build_status_flow(
    video_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _signup(video_client, caplog, email="flow@example.com")
    job_id = _create_completed_youtube_job(video_client, "ccccccccccc")

    start = video_client.post(f"/api/jobs/{job_id}/video-download")
    assert start.status_code == 200
    assert start.json()["status"] in ("building", "ready")

    status = {}
    for _ in range(50):
        status = video_client.get(f"/api/jobs/{job_id}/video-download").json()
        if status["status"] == "ready":
            break
        time.sleep(0.05)
    else:
        pytest.fail("video build did not become ready")

    assert status["download_url"] == f"/media/{job_id}/video?download=1"
    assert status["download_filename"].endswith("(Remastered).mp4")

    media = video_client.get(f"/media/{job_id}/video?download=1")
    assert media.status_code == 200
    assert "video/mp4" in media.headers.get("content-type", "")


def test_double_start_while_building(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("REMASTERED_WEB_MAX_CONCURRENT_VIDEO_BUILDS", "2")
    mgr = VideoBuildManager(work)
    gate = threading.Event()
    started = threading.Event()

    def slow_build(job, *, work_dir=None, settings=None):
        started.set()
        assert gate.wait(timeout=5)
        out = built_video_path(job, work_dir or work)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"v")
        return out

    monkeypatch.setattr("remastered.web.job_video_download.run_video_build", slow_build)
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 10, "filesize": 1, "title": "t"},
    )

    ws = job_workspace_dir(work, "u", "job1")
    ws.mkdir(parents=True)
    mastered = ws / "master.wav"
    mastered.write_bytes(b"1")
    job = {
        "id": "job1",
        "user_id": "u",
        "youtube_url": "https://youtu.be/ccccccccccc",
        "mastered_path": str(mastered),
        "status": "completed",
    }

    mgr.start_build(job)
    assert started.wait(timeout=2)
    second = mgr.start_build(job)
    assert second["status"] == "building"
    gate.set()
    for _ in range(40):
        if mgr.status_payload(job)["status"] == "ready":
            break
        time.sleep(0.05)
    else:
        pytest.fail("build did not finish")


def test_duration_limit_blocks_video_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"x")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_WEB_MAX_SOURCE_DURATION_SECONDS", "60")
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")

    def inspect(url):
        return {"duration": 60, "filesize": 1000, "title": "Too long"}

    monkeypatch.setattr("remastered.web.limits.inspect_youtube_source", inspect)
    monkeypatch.setattr(
        "remastered.web.job_video_download.run_video_build",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not build")),
    )

    with TestClient(fastapi_application) as client:
        _signup(client, caplog, email="long-vid@example.com")
        job_id = _create_completed_youtube_job(client, "bbbbbbbbbbb")
        monkeypatch.setattr(
            "remastered.web.limits.inspect_youtube_source",
            lambda url: {"duration": 120, "filesize": 1000, "title": "Too long"},
        )
        resp = client.post(f"/api/jobs/{job_id}/video-download")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert "limit" in (body.get("error") or "").lower()


def test_delete_job_removes_built_video(
    video_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _signup(video_client, caplog, email="delete-vid@example.com")
    job_id = _create_completed_youtube_job(video_client, "ddddddddddd")
    video_client.post(f"/api/jobs/{job_id}/video-download")
    for _ in range(50):
        if video_client.get(f"/api/jobs/{job_id}/video-download").json()["status"] == "ready":
            break
        time.sleep(0.05)

    work: Path = video_client.work_dir  # type: ignore[attr-defined]
    store = JobStore(work / "remastered_jobs.db")
    job = store.get_job(job_id)
    assert job
    path = built_video_path(job, work)
    assert path.is_file()

    delete = video_client.delete(f"/api/jobs/{job_id}")
    assert delete.status_code == 200
    assert not path.is_file()


def test_cleanup_expired_video_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("REMASTERED_WEB_VIDEO_DOWNLOAD_CACHE_TTL_SECONDS", "1")
    mgr = init_video_build_manager(work)
    ws = job_workspace_dir(work, "user-1", "job-cache")
    ws.mkdir(parents=True)
    out = ws / BUILT_VIDEO_FILENAME
    out.write_bytes(b"mp4")

    with mgr._lock:
        state = mgr._state_for("job-cache")
        state.phase = VideoBuildPhase.READY
        state.expires_at = time.monotonic() - 1
        state.output_path = out

    removed = mgr.cleanup_expired_files()
    assert removed == 1
    assert not out.is_file()


def test_job_video_download_eligible_requires_mastered(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    job = {
        "status": "completed",
        "youtube_url": "https://youtu.be/abc12345678",
        "mastered_path": "",
        "id": "x",
        "user_id": "u",
    }
    assert not job_video_download_eligible(job, work)


def test_run_video_build_uses_ytdlp_and_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    ws = job_workspace_dir(work, "u", "mux-job")
    ws.mkdir(parents=True)
    mastered = ws / "master.wav"
    mastered.write_bytes(b"wav")
    job = {
        "id": "mux-job",
        "user_id": "u",
        "youtube_url": "https://youtu.be/eeeeeeeeeee",
        "mastered_path": str(mastered),
        "status": "completed",
    }
    video_path = ws / "source.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr(
        "remastered.web.job_video_download.validate_youtube_source_limits",
        lambda url: None,
    )
    monkeypatch.setattr(
        "remastered.web.job_video_download.download_youtube_video_stream",
        lambda url, out_dir, yt_dlp_path=None: video_path,
    )
    captured: dict[str, list] = {"cmd": []}

    def fake_mux(video, audio, output, ffmpeg_path="ffmpeg"):
        captured["cmd"] = [str(video), str(audio), str(output)]
        output.write_bytes(b"out")
        return output

    monkeypatch.setattr(
        "remastered.web.job_video_download.mux_remastered_audio_into_video",
        fake_mux,
    )

    out = run_video_build(job, work_dir=work)
    assert out.is_file()
    assert captured["cmd"][0] == str(video_path)
    assert captured["cmd"][1] == str(mastered.resolve())
