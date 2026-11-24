from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application
from remastered.web.cleanup import cleanup_old_jobs, remove_job_record_and_media
from remastered.web.db import JobStore
from remastered.web.job_media_paths import job_workspace_dir
from remastered.web.job_video_download import (
    BUILT_VIDEO_FILENAME,
    init_video_build_manager,
)
from remastered.web.retention_exempt import (
    parse_retention_exempt_emails,
    reset_retention_exempt_emails_cache,
)
from remastered.web.stream_audio import playback_path_for_wav


def _set_created_at(store: JobStore, job_id: str, created_at: str) -> None:
    with store._conn() as conn:
        conn.execute(
            "UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (created_at, created_at, job_id),
        )


def _old_job(
    store: JobStore,
    work: Path,
    *,
    user_id: str,
    suffix: str,
) -> tuple[str, Path, Path, Path]:
    job_id = store.create_job(
        user_id=user_id,
        youtube_url=f"https://www.youtube.com/watch?v=ret{suffix}",
        reference_path="/references/ref.wav",
        title="Retention test",
        description="",
        privacy="private",
        upload_enabled=False,
    )
    store.update_job(job_id, status="completed", stage="done")
    old_created = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat().replace("+00:00", "Z")
    _set_created_at(store, job_id, old_created)

    ws = job_workspace_dir(work, user_id, job_id)
    ws.mkdir(parents=True)
    audio = ws / "source.wav"
    mastered = ws / "master.wav"
    audio.write_bytes(b"audio")
    mastered.write_bytes(b"master")
    playback = playback_path_for_wav(mastered)
    playback.parent.mkdir(parents=True, exist_ok=True)
    playback.write_bytes(b"m4a-preview")
    store.update_job(
        job_id,
        audio_path=str(audio),
        mastered_path=str(mastered),
    )
    return job_id, audio, mastered, playback


@pytest.fixture
def work_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("REMASTERED_WEB_JOB_RETENTION_DAYS", "7")
    monkeypatch.setenv("REMASTERED_WEB_VIDEO_DOWNLOAD_CACHE_TTL_SECONDS", "1")
    reset_retention_exempt_emails_cache()
    return work


def test_parse_retention_exempt_emails_case_insensitive() -> None:
    emails = parse_retention_exempt_emails("  Owner@Example.COM , other@test.com ")
    assert "owner@example.com" in emails
    assert "other@test.com" in emails


def test_exempt_job_survives_retention_sweep(
    work_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = JobStore(work_env / "remastered_jobs.db")
    owner = store.create_auth_user(login_id="BlueDSkim@Gmail.com", password_hash="x")
    job_id, audio, mastered, playback = _old_job(
        store, work_env, user_id=owner, suffix="exempt"
    )

    caplog.set_level("INFO")
    removed = cleanup_old_jobs(store, work_env)
    assert removed == 0

    job = store.get_job(job_id)
    assert job is not None
    assert job["files_cleaned"] == 0
    assert audio.is_file()
    assert mastered.is_file()
    assert playback.is_file()
    assert any(
        "Retention sweep skipped 1 job(s) owned by exempt accounts" in r.message
        for r in caplog.records
    )


def test_non_exempt_job_still_deleted(
    work_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(work_env / "remastered_jobs.db")
    owner = store.create_auth_user(login_id="regular@example.com", password_hash="x")
    job_id, audio, mastered, playback = _old_job(
        store, work_env, user_id=owner, suffix="plain"
    )

    removed = cleanup_old_jobs(store, work_env)
    assert removed == 1

    job = store.get_job(job_id)
    assert job is not None
    assert job["files_cleaned"] == 1
    assert not audio.is_file()
    assert not mastered.is_file()
    assert not playback.is_file()


def test_env_override_replaces_default_exempt_list(
    work_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "REMASTERED_WEB_RETENTION_EXEMPT_EMAILS", "other-owner@example.com"
    )
    reset_retention_exempt_emails_cache()

    store = JobStore(work_env / "remastered_jobs.db")
    blue = store.create_auth_user(login_id="bluedskim@gmail.com", password_hash="x")
    other = store.create_auth_user(
        login_id="other-owner@example.com", password_hash="x"
    )
    blue_id, blue_audio, _, _ = _old_job(store, work_env, user_id=blue, suffix="blue")
    other_id, other_audio, _, _ = _old_job(store, work_env, user_id=other, suffix="oth")

    removed = cleanup_old_jobs(store, work_env)
    assert removed == 1
    assert store.get_job(blue_id)["files_cleaned"] == 1
    assert not blue_audio.is_file()
    assert store.get_job(other_id)["files_cleaned"] == 0
    assert other_audio.is_file()
    reset_retention_exempt_emails_cache()


def test_video_cache_sweep_still_runs_for_exempt_job(work_env: Path) -> None:
    store = JobStore(work_env / "remastered_jobs.db")
    owner = store.create_auth_user(login_id="bluedskim@gmail.com", password_hash="x")
    job_id, audio, mastered, _ = _old_job(
        store, work_env, user_id=owner, suffix="vid"
    )

    ws = job_workspace_dir(work_env, owner, job_id)
    built_video = ws / BUILT_VIDEO_FILENAME
    built_video.write_bytes(b"cached-mp4")
    stale_mtime = time.time() - 7200
    os.utime(built_video, (stale_mtime, stale_mtime))

    init_video_build_manager(work_env)
    cleanup_old_jobs(store, work_env)

    job = store.get_job(job_id)
    assert job is not None
    assert job["files_cleaned"] == 0
    assert audio.is_file()
    assert mastered.is_file()
    # On-demand MP4 cache is rebuildable; retention must not touch result WAV/preview.
    assert not built_video.is_file()


def test_manual_delete_still_works_for_exempt_owner(work_env: Path) -> None:
    store = JobStore(work_env / "remastered_jobs.db")
    owner = store.create_auth_user(login_id="bluedskim@gmail.com", password_hash="x")
    job_id, audio, mastered, _ = _old_job(
        store, work_env, user_id=owner, suffix="del"
    )
    job = store.get_job(job_id)
    assert job is not None
    assert remove_job_record_and_media(store, job, work_env)
    assert store.get_job(job_id) is None
    assert not audio.is_file()
    assert not mastered.is_file()


@pytest.fixture
def web_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"test")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    reset_retention_exempt_emails_cache()
    with TestClient(fastapi_application) as client:
        yield client


def _signup(client: TestClient, caplog: pytest.LogCaptureFixture, email: str) -> None:
    caplog.set_level("INFO")
    client.post("/auth/send-verification", data={"login_id": email})
    code = None
    for record in caplog.records:
        match = re.search(r"Email verification code for .+: (\d{6})", record.message)
        if match:
            code = match.group(1)
    assert code
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


def test_home_shows_kept_until_delete_for_exempt_account(
    web_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _signup(web_client, caplog, "bluedskim@gmail.com")
    resp = web_client.get("/")
    assert resp.status_code == 200
    assert "kept until you delete" in resp.text.lower()
    assert "deleted automatically after" not in resp.text.lower()