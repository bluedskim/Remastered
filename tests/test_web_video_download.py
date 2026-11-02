from __future__ import annotations

import json
import re
import shutil
import subprocess
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
    build_still_image_video_with_audio,
    built_video_path,
    init_video_build_manager,
    job_is_youtube_sourced,
    job_video_download_eligible,
    prepare_still_image_for_job,
    run_video_build,
)
from remastered.web.video_download_access import reset_video_download_allowed_emails_cache


@pytest.fixture(autouse=True)
def _clear_video_download_allowlist_cache() -> None:
    reset_video_download_allowed_emails_cache()
    yield
    reset_video_download_allowed_emails_cache()


@pytest.fixture
def jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PACKAGE_DIR / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )


def _allow_test_emails(monkeypatch: pytest.MonkeyPatch, *emails: str) -> None:
    monkeypatch.setenv(
        "REMASTERED_WEB_VIDEO_DOWNLOAD_ALLOWED_EMAILS",
        ",".join(emails),
    )
    reset_video_download_allowed_emails_cache()


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
    _allow_test_emails(monkeypatch, "flow@example.com", "delete-vid@example.com")

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


def test_anon_cannot_start_video_build(
    video_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_test_emails(monkeypatch, "bluedskim@gmail.com")
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


def test_non_allowlisted_user_gets_403_and_hidden_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
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
    _allow_test_emails(monkeypatch, "bluedskim@gmail.com")

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 60, "filesize": 1000, "title": "T"},
    )

    with TestClient(fastapi_application) as client:
        _signup(client, caplog, email="other@example.com")
        job_id = _create_completed_youtube_job(client, "aaaaaaaaaaa")
        panel = client.get(f"/jobs/{job_id}/panel")
        assert panel.status_code == 286
        assert "Download video" not in panel.text

        resp = client.post(f"/api/jobs/{job_id}/video-download")
        assert resp.status_code == 403
        assert client.get(f"/api/jobs/{job_id}/video-download").status_code == 403


def test_allowlist_env_override(
    video_client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_test_emails(monkeypatch, "custom@example.com")
    _signup(video_client, caplog, email="custom@example.com")
    job_id = _create_completed_youtube_job(video_client, "bbbbbbbbbbb")
    resp = video_client.post(f"/api/jobs/{job_id}/video-download")
    assert resp.status_code == 200


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


def test_run_video_build_uses_ffmpeg_still_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    ws = job_workspace_dir(work, "u", "mux-job")
    ws.mkdir(parents=True)
    mastered = ws / "master.wav"
    mastered.write_bytes(b"wav")
    thumb = work / "thumbs" / "eeeeeeeeeee.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"jpeg-bytes")
    job = {
        "id": "mux-job",
        "user_id": "u",
        "youtube_url": "https://youtu.be/eeeeeeeeeee",
        "mastered_path": str(mastered),
        "status": "completed",
    }

    monkeypatch.setattr(
        "remastered.web.job_video_download.resolve_job_thumbnail_path",
        lambda j, wd: thumb,
    )
    captured: dict[str, list] = {"cmd": []}

    def fake_build(still, audio, output, ffmpeg_path="ffmpeg"):
        captured["cmd"] = [str(still), str(audio), str(output), ffmpeg_path]
        output.write_bytes(b"out")
        return output

    monkeypatch.setattr(
        "remastered.web.job_video_download.build_still_image_video_with_audio",
        fake_build,
    )

    out = run_video_build(job, work_dir=work)
    assert out.is_file()
    assert captured["cmd"][0] == str(thumb)
    assert captured["cmd"][1] == str(mastered.resolve())


def test_build_still_image_video_ffmpeg_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    still = ws / "still.jpg"
    still.write_bytes(b"x")
    audio = ws / "a.wav"
    audio.write_bytes(b"a")
    out = ws / "out.mp4"
    ran: dict[str, object] = {}

    def fake_run(cmd, capture_output, text):
        ran["cmd"] = cmd
        out.write_bytes(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("remastered.web.job_video_download.subprocess.run", fake_run)
    build_still_image_video_with_audio(still, audio, out, ffmpeg_path="/usr/bin/ffmpeg")
    cmd = ran["cmd"]
    assert isinstance(cmd, list)
    joined = " ".join(cmd)
    assert "-loop" in cmd and "1" in cmd
    assert "libx264" in joined
    assert "stillimage" in joined
    assert "yuv420p" in joined
    assert "+faststart" in joined
    assert "-ar" in cmd and "48000" in cmd
    assert "-ac" in cmd and "2" in cmd
    assert "-shortest" in cmd
    assert "yt-dlp" not in joined.lower()


def test_no_thumbnail_fallback_still(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    tmp_dir = work / "tmp"
    tmp_dir.mkdir(parents=True)
    ws = job_workspace_dir(work, "u", "fallback-job")
    ws.mkdir(parents=True)
    mastered = ws / "master.wav"
    mastered.write_bytes(b"wav")
    job = {
        "id": "fallback-job",
        "user_id": "u",
        "youtube_url": "https://youtu.be/ggggggggggg",
        "title": "Fallback Title",
        "mastered_path": str(mastered),
        "status": "completed",
    }
    monkeypatch.setattr(
        "remastered.web.job_video_download.resolve_job_thumbnail_path",
        lambda j, wd: None,
    )
    generated: dict[str, Path] = {}

    def fake_generate(output_path, *, title=None, ffmpeg_path="ffmpeg"):
        generated["path"] = output_path
        generated["title"] = title  # type: ignore[assignment]
        output_path.write_bytes(b"png")
        return output_path

    monkeypatch.setattr(
        "remastered.web.job_video_download.generate_fallback_still_image",
        fake_generate,
    )
    still = prepare_still_image_for_job(job, work, tmp_dir, ffmpeg_path="ffmpeg")
    assert still.is_file()
    assert generated["title"] == "Fallback Title"


def test_ffprobe_real_build_matches_spec(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not available")

    work = tmp_path / "work"
    ws = job_workspace_dir(work, "u", "probe-job")
    ws.mkdir(parents=True)
    audio = ws / "tone.wav"
    still = ws / "frame.jpg"
    # 2.5s mono sine wav + small jpeg via ffmpeg
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2.5",
            str(audio),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x360:duration=1",
            "-frames:v",
            "1",
            str(still),
        ],
        check=True,
        capture_output=True,
    )
    out = ws / "built.mp4"
    build_still_image_video_with_audio(still, audio, out)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(probe.stdout)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio_s = next(s for s in data["streams"] if s["codec_type"] == "audio")
    duration = float(data["format"]["duration"])

    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert int(video["width"]) == 1920
    assert int(video["height"]) == 1080
    assert audio_s["codec_name"] == "aac"
    assert int(audio_s["sample_rate"]) == 48000
    assert int(audio_s["channels"]) == 2
    assert abs(duration - 2.5) <= 0.5
