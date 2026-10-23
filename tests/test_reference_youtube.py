from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.db import SCHEMA_VERSION, JobStore
from remastered.web.reference_display import reference_ui_for_job
from remastered.web.reference_youtube import (
    SAME_VIDEO_ERROR,
    ensure_reference_differs_from_source,
    parse_reference_youtube_url,
    youtube_reference_path_key,
)
from remastered.web.worker import JobWorker
from remastered.config import resolve_settings


def test_parse_reference_youtube_url_normalizes() -> None:
    vid = "dQw4w9WgXcQ"
    url, video_id = parse_reference_youtube_url(
        f"https://www.youtube.com/watch?v={vid}&list=RD{vid}&start_radio=1"
    )
    assert video_id == vid
    assert url == f"https://www.youtube.com/watch?v={vid}"
    assert youtube_reference_path_key(vid) == f"youtube-ref:{vid}"


def test_same_video_rejected() -> None:
    vid = "abc123XYZ01"
    source = f"https://www.youtube.com/watch?v={vid}"
    ref = f"https://youtu.be/{vid}"
    with pytest.raises(ValueError, match=SAME_VIDEO_ERROR):
        ensure_reference_differs_from_source(source, ref)


def test_reference_ui_for_youtube_job() -> None:
    ui = reference_ui_for_job(
        {
            "reference_source_type": "youtube",
            "reference_video_id": "vid1",
            "reference_youtube_title": "My Ref",
            "reference_youtube_url": "https://www.youtube.com/watch?v=vid1",
            "reference_path": "youtube-ref:vid1",
        }
    )
    assert ui["kind"] == "youtube"
    assert ui["genre"] == "YouTube"
    assert ui["label"] == "My Ref"
    assert ui["url"] == "https://www.youtube.com/watch?v=vid1"


def _create_v7_jobs_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            youtube_url TEXT NOT NULL,
            reference_path TEXT NOT NULL,
            title TEXT,
            description TEXT NOT NULL DEFAULT '',
            privacy TEXT NOT NULL DEFAULT 'private',
            upload_enabled INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            source_video_id TEXT,
            uploaded_video_id TEXT,
            audio_path TEXT,
            mastered_path TEXT,
            video_path TEXT,
            stage_state TEXT NOT NULL DEFAULT '{}',
            files_cleaned INTEGER NOT NULL DEFAULT 0,
            client_ip TEXT,
            stereo_width_mode TEXT NOT NULL DEFAULT 'follow_reference'
        );
        CREATE TABLE job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            message TEXT NOT NULL
        );
        """
    )
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()


def test_migration_v8_reference_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    _create_v7_jobs_database(db_path)
    JobStore(db_path)
    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()
    assert version == SCHEMA_VERSION
    assert "reference_source_type" in cols
    assert "reference_youtube_url" in cols
    assert "reference_video_id" in cols
    assert "reference_youtube_title" in cols


@pytest.fixture
def youtube_ref_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"test-audio")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.delenv("REMASTERED_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )

    def fake_resolve(job, settings, log=None):
        video_id = job.get("reference_video_id") or "refvid"
        path = settings.work_dir / "reference_cache" / f"{video_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF" + b"\x00" * 8)
        return path, "Reference From YouTube"

    monkeypatch.setattr("remastered.web.worker.resolve_reference_wav", fake_resolve)

    def fake_web_pipeline(url, reference, settings, progress=None, on_downloaded=None, stereo_width=None):
        if on_downloaded:
            on_downloaded("Source Track", "srcvid")
        audio = settings.work_dir / "srcvid" / "audio.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"RIFF" + b"\x00" * 100)
        mastered = settings.work_dir / "srcvid" / "master" / "out.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        mastered.write_bytes(b"RIFF" + b"\x00" * 100)
        return {"audio": audio, "mastered": mastered, "video_id": "srcvid", "title": "Source Track"}

    monkeypatch.setattr("remastered.web.worker.run_web_master_pipeline", fake_web_pipeline)
    monkeypatch.setattr(
        "remastered.web.worker.transcode_wav_for_playback",
        lambda wav: wav.with_suffix(".m4a"),
    )
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "t"},
    )

    from remastered.web.app import fastapi_application

    with TestClient(fastapi_application) as test_client:
        yield test_client


def test_form_shows_reference_youtube_field(youtube_ref_client: TestClient) -> None:
    home = youtube_ref_client.get("/")
    assert 'id="reference_youtube_url"' in home.text
    assert "this video's audio is used as the reference" in home.text


def test_create_job_reference_youtube_overrides_preset(youtube_ref_client: TestClient) -> None:
    store = youtube_ref_client.app.state.store
    resp = youtube_ref_client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "reference_name": "ref.wav",
            "reference_youtube_url": "https://www.youtube.com/watch?v=abc123XYZ99",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].split("/")[-1]
    job = store.get_job(job_id)
    assert job is not None
    assert job["reference_source_type"] == "youtube"
    assert job["reference_video_id"] == "abc123XYZ99"
    assert job["reference_path"] == "youtube-ref:abc123XYZ99"


def test_same_source_and_reference_rejected(youtube_ref_client: TestClient) -> None:
    vid = "dQw4w9WgXcQ"
    resp = youtube_ref_client.post(
        "/jobs",
        data={
            "youtube_url": f"https://www.youtube.com/watch?v={vid}",
            "reference_youtube_url": f"https://youtu.be/{vid}",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert SAME_VIDEO_ERROR in resp.text


def test_job_page_renders_youtube_reference(youtube_ref_client: TestClient) -> None:
    store = youtube_ref_client.app.state.store
    youtube_ref_client.get("/")
    resp = youtube_ref_client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "reference_youtube_url": "https://www.youtube.com/watch?v=abc123XYZ99",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].split("/")[-1]
    for _ in range(40):
        job = store.get_job(job_id)
        if job and job["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("job did not complete")

    detail = youtube_ref_client.get(f"/jobs/{job_id}")
    assert detail.status_code == 200
    assert "Reference From YouTube" in detail.text
    assert "watch?v=abc123XYZ99" in detail.text


def test_worker_reference_download_failure_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    store = JobStore(work / "jobs.db")
    job_id = store.create_job(
        user_id="anon-fail",
        youtube_url="https://www.youtube.com/watch?v=src12345678",
        reference_path="youtube-ref:ref12345678",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
        reference_source_type="youtube",
        reference_youtube_url="https://www.youtube.com/watch?v=ref12345678",
        reference_video_id="ref12345678",
    )

    def boom(job, settings, log=None):
        raise RuntimeError("Could not download the reference video. (timeout)")

    monkeypatch.setattr("remastered.web.worker.resolve_reference_wav", boom)
    monkeypatch.setattr("remastered.web.worker.validate_youtube_source_limits", lambda url: None)

    worker = JobWorker(store, resolve_settings())
    worker.start()
    try:
        for _ in range(50):
            job = store.get_job(job_id)
            assert job is not None
            if job["status"] == "failed":
                break
            time.sleep(0.05)
        else:
            pytest.fail("worker did not fail job")
        assert job["error"] == "Could not download the reference video."
    finally:
        worker.stop()
