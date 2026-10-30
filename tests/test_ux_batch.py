from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application, PRIVACY_NOTICE
from remastered.web.db import SCHEMA_VERSION, JobStore
from remastered.web.processing_stage import STAGE_MATCHING, pipeline_steps_for_job
from remastered.web.youtube_meta import YouTubeVideoMeta, clear_youtube_meta_cache


@pytest.fixture
def ux_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    clear_youtube_meta_cache()
    with TestClient(fastapi_application) as client:
        yield client
    clear_youtube_meta_cache()


def test_youtube_meta_includes_duration(ux_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str) -> YouTubeVideoMeta:
        return YouTubeVideoMeta(
            video_id="abc123XYZ99",
            title="Long Song",
            author_name="Artist",
            thumbnail_url="https://i.ytimg.com/vi/abc123XYZ99/hqdefault.jpg",
            duration_seconds=1200,
        )

    monkeypatch.setattr("remastered.web.app.fetch_youtube_video_meta", fake_fetch)
    resp = ux_client.get(
        "/api/youtube/meta",
        params={"url": "https://www.youtube.com/watch?v=abc123XYZ99"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["duration_seconds"] == 1200
    assert data["duration_label"] == "20:00"
    assert data["exceeds_source_limit"] is True


def test_privacy_page(ux_client: TestClient) -> None:
    resp = ux_client.get("/privacy")
    assert resp.status_code == 200
    assert PRIVACY_NOTICE in resp.text
    assert "14 days" in resp.text
    assert "Back to home" in resp.text


def test_opensource_page(ux_client: TestClient) -> None:
    resp = ux_client.get("/opensource")
    assert resp.status_code == 200
    assert "Open source" in resp.text
    assert "matchering" in resp.text
    assert "Back to home" in resp.text


def test_footer_includes_contact_mailto(ux_client: TestClient) -> None:
    resp = ux_client.get("/privacy")
    assert resp.status_code == 200
    assert 'href="mailto:bluedskim@gmail.com"' in resp.text
    assert ">Contact</a>" in resp.text


def test_home_has_source_preview_and_listen_toggle_markup(ux_client: TestClient) -> None:
    home = ux_client.get("/")
    assert home.status_code == 200
    assert 'id="source-youtube-preview"' in home.text
    assert "source-duration-warning" in home.text
    assert "youtube-preview.js" in home.text
    assert 'aria-label="Clear URL"' in home.text
    assert "url-input-clear" in home.text


def _create_v8_jobs_database(path: Path) -> None:
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
            stereo_width_mode TEXT NOT NULL DEFAULT 'follow_reference',
            reference_source_type TEXT NOT NULL DEFAULT 'preset',
            reference_youtube_url TEXT,
            reference_video_id TEXT,
            reference_youtube_title TEXT
        );
        """
    )
    conn.execute("PRAGMA user_version = 8")
    conn.commit()
    conn.close()


def test_migration_v9_processing_stage(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    _create_v8_jobs_database(db_path)
    JobStore(db_path)
    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()
    assert version == SCHEMA_VERSION
    assert "processing_stage" in cols


def test_pipeline_steps_show_failed_stage() -> None:
    steps = pipeline_steps_for_job(
        {
            "status": "failed",
            "processing_stage": STAGE_MATCHING,
            "reference_source_type": "preset",
            "reference_path": "/refs/a.wav",
        }
    )
    failed = [s for s in steps if s["state"] == "failed"]
    assert failed and failed[0]["label"] == "Matching"


def test_job_page_has_ab_listen_toggle(ux_client: TestClient) -> None:
    players = (Path(__file__).resolve().parents[1] / "src/remastered/web/templates/partials/job_players.html").read_text()
    assert "data-ab-listen-toggle" in players
    panel = (
        Path(__file__).resolve().parents[1]
        / "src/remastered/web/templates/partials/job_pipeline_stages.html"
    ).read_text()
    assert "pipeline-stepper" in panel
