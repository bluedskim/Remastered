from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.config import resolve_settings
from remastered.web.app import fastapi_application
from remastered.web.db import SCHEMA_VERSION, JobStore
from remastered.web.job_memo import JOB_MEMO_MAX_LENGTH, normalize_job_memo
from remastered.web.video_download_access import reset_video_download_allowed_emails_cache


@pytest.fixture(autouse=True)
def _clear_allowlist_cache() -> None:
    reset_video_download_allowed_emails_cache()
    yield
    reset_video_download_allowed_emails_cache()


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
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "Memo Track"},
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


def _create_job(client: TestClient, url: str = "https://www.youtube.com/watch?v=memo1") -> str:
    client.get("/")
    resp = client.post(
        "/jobs",
        data={"youtube_url": url, "reference_name": "ref.wav"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return resp.headers["location"].split("/")[-1]


def test_migration_adds_memo_column(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
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
            reference_youtube_title TEXT,
            processing_stage TEXT NOT NULL DEFAULT 'queued',
            final_tp_protection INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            message TEXT NOT NULL
        );
        PRAGMA user_version = 14;
        """
    )
    conn.commit()
    conn.close()

    JobStore(db_path)
    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()

    assert version == SCHEMA_VERSION
    assert "memo" in cols


def test_normalize_job_memo_trims_and_limits() -> None:
    assert normalize_job_memo("  hello  ") == ("hello", None)
    assert normalize_job_memo("") == ("", None)
    assert normalize_job_memo(None) == ("", None)
    korean_100 = "가" * JOB_MEMO_MAX_LENGTH
    assert normalize_job_memo(korean_100) == (korean_100, None)
    korean_101 = "가" * (JOB_MEMO_MAX_LENGTH + 1)
    memo, err = normalize_job_memo(korean_101)
    assert memo is None
    assert err is not None


def test_store_save_and_load_memo(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    job_id = store.create_job(
        user_id="user-1",
        youtube_url="https://www.youtube.com/watch?v=x",
        reference_path="/tmp/ref.wav",
        title="T",
        description="",
        privacy="private",
        upload_enabled=False,
    )
    store.update_job(job_id, memo="My note")
    job = store.get_job(job_id)
    assert job is not None
    assert job["memo"] == "My note"


def test_owner_can_save_and_reload_memo(client: TestClient) -> None:
    job_id = _create_job(client)
    put = client.put(f"/api/jobs/{job_id}/memo", json={"memo": "  Ship it  "})
    assert put.status_code == 200
    assert put.json()["memo"] == "Ship it"

    detail = client.get(f"/jobs/{job_id}")
    assert detail.status_code == 200
    assert "Memo" in detail.text
    assert "data-job-memo" in detail.text
    assert "Ship it" in detail.text

    store = JobStore(resolve_settings().work_dir / "remastered_jobs.db")
    job = store.get_job(job_id)
    assert job is not None
    assert job["memo"] == "Ship it"


def test_korean_boundary_and_reject_overlong(client: TestClient) -> None:
    job_id = _create_job(client, "https://www.youtube.com/watch?v=memo-kr")
    ok = client.put(f"/api/jobs/{job_id}/memo", json={"memo": "가" * 100})
    assert ok.status_code == 200
    assert ok.json()["memo"] == "가" * 100

    bad = client.put(f"/api/jobs/{job_id}/memo", json={"memo": "가" * 101})
    assert bad.status_code == 400
    assert "100" in bad.json()["detail"]


def test_clear_memo(client: TestClient) -> None:
    job_id = _create_job(client, "https://www.youtube.com/watch?v=memo-clear")
    client.put(f"/api/jobs/{job_id}/memo", json={"memo": "temporary"})
    cleared = client.put(f"/api/jobs/{job_id}/memo", json={"memo": "   "})
    assert cleared.status_code == 200
    assert cleared.json()["memo"] == ""


def test_non_owner_cannot_read_or_write_memo(client: TestClient) -> None:
    job_id = _create_job(client)
    client.put(f"/api/jobs/{job_id}/memo", json={"memo": "secret"})

    with TestClient(fastapi_application) as other:
        other.get("/")
        assert other.get(f"/jobs/{job_id}").status_code == 404
        assert other.put(f"/api/jobs/{job_id}/memo", json={"memo": "hack"}).status_code == 404


def test_memo_persists_across_panel_poll(client: TestClient) -> None:
    job_id = _create_job(client)
    client.put(f"/api/jobs/{job_id}/memo", json={"memo": "during run"})
    for _ in range(5):
        panel = client.get(f"/jobs/{job_id}/panel")
        assert panel.status_code in (200, 286)
        time.sleep(0.02)
    detail = client.get(f"/jobs/{job_id}")
    assert "during run" in detail.text


def test_memo_survives_after_job_completes(client: TestClient) -> None:
    job_id = _create_job(client)
    client.put(f"/api/jobs/{job_id}/memo", json={"memo": "kept"})
    _wait_job_complete(client, job_id)
    detail = client.get(f"/jobs/{job_id}")
    assert "kept" in detail.text
    assert 'id="job-memo"' in detail.text
