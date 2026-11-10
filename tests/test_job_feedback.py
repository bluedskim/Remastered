from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
import sqlite3
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application
from remastered.web.db import SCHEMA_VERSION, JobStore
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
        lambda url: {"duration": 120, "filesize": 1000, "title": "Feedback Track"},
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


def _create_completed_job(client: TestClient, url: str = "https://www.youtube.com/watch?v=fbk1") -> str:
    client.get("/")
    resp = client.post(
        "/jobs",
        data={"youtube_url": url, "reference_name": "ref.wav"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].split("/")[-1]
    _wait_job_complete(client, job_id)
    return job_id


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


def test_migration_adds_job_feedback_table(tmp_path: Path) -> None:
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
        PRAGMA user_version = 13;
        """
    )
    conn.commit()
    conn.close()

    store = JobStore(db_path)
    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    assert version == SCHEMA_VERSION
    assert "job_feedback" in tables


def test_feedback_requires_sign_in(client: TestClient) -> None:
    job_id = _create_completed_job(client)
    resp = client.post(
        f"/api/jobs/{job_id}/feedback",
        json={"rating": 1},
    )
    assert resp.status_code == 401


def test_vote_toggle_and_switch(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    _signup(client, caplog, "voter@example.com")
    job_id = _create_completed_job(client)

    up = client.post(f"/api/jobs/{job_id}/feedback", json={"rating": 1})
    assert up.status_code == 200
    assert up.json()["rating"] == 1
    assert up.json()["counts"]["up"] == 1

    clear = client.post(f"/api/jobs/{job_id}/feedback", json={"rating": 1})
    assert clear.status_code == 200
    assert clear.json()["rating"] is None
    assert clear.json()["counts"]["up"] == 0

    down = client.post(f"/api/jobs/{job_id}/feedback", json={"rating": -1})
    assert down.status_code == 200
    assert down.json()["rating"] == -1

    switch = client.post(f"/api/jobs/{job_id}/feedback", json={"rating": 1})
    assert switch.status_code == 200
    assert switch.json()["rating"] == 1
    assert switch.json()["counts"]["up"] == 1
    assert switch.json()["counts"]["down"] == 0


def test_other_user_cannot_vote(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    _signup(client, caplog, "owner-fb@example.com")
    job_id = _create_completed_job(client)

    other = TestClient(fastapi_application)
    _signup(other, caplog, "stranger-fb@example.com")
    denied = other.post(f"/api/jobs/{job_id}/feedback", json={"rating": 1})
    assert denied.status_code == 404


def test_non_completed_job_rejected(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    _signup(client, caplog, "pending-fb@example.com")
    client.get("/")
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=runfb",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    job_id = resp.headers["location"].split("/")[-1]
    blocked = client.post(f"/api/jobs/{job_id}/feedback", json={"rating": 1})
    assert blocked.status_code == 400
    assert "completed" in blocked.json()["detail"].lower()


def test_completed_page_shows_feedback_for_signed_in(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    job_id = _create_completed_job(client)
    anon = client.get(f"/jobs/{job_id}")
    assert "data-job-feedback" not in anon.text

    _signup(client, caplog, "viewer-fb@example.com")
    signed = client.get(f"/jobs/{job_id}")
    assert 'data-job-feedback' in signed.text
    assert "Rate this result" in signed.text
    assert "job-feedback.js" in signed.text
    assert "data-feedback-counts" not in signed.text


def test_completed_page_hides_vote_counts_even_for_admin(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "REMASTERED_WEB_VIDEO_DOWNLOAD_ALLOWED_EMAILS",
        "admin-nocounts@example.com",
    )
    reset_video_download_allowed_emails_cache()
    _signup(client, caplog, "admin-nocounts@example.com")
    job_id = _create_completed_job(client)
    page = client.get(f"/jobs/{job_id}")
    assert page.status_code == 200
    assert "data-feedback-counts" not in page.text
    assert "data-feedback-rating" in page.text


def test_admin_feedback_list_access(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "REMASTERED_WEB_VIDEO_DOWNLOAD_ALLOWED_EMAILS",
        "admin-fb@example.com",
    )
    reset_video_download_allowed_emails_cache()
    _signup(client, caplog, "admin-fb@example.com")
    job_id = _create_completed_job(client)
    client.post(f"/api/jobs/{job_id}/feedback", json={"rating": 1})

    page = client.get("/admin/feedback")
    assert page.status_code == 200
    assert job_id in page.text
    assert "Up" in page.text
    assert "admin-fb@example.com" in page.text

    other = TestClient(fastapi_application)
    _signup(other, caplog, "nonadmin-fb@example.com")
    assert other.get("/admin/feedback").status_code == 403


def test_home_job_list_shows_user_feedback_rating(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _signup(client, caplog, "list-fb@example.com")
    job_rated = _create_completed_job(client, url="https://www.youtube.com/watch?v=listrated")
    job_plain = _create_completed_job(client, url="https://www.youtube.com/watch?v=listplain")
    client.post(f"/api/jobs/{job_rated}/feedback", json={"rating": 1})

    home = client.get("/")
    assert home.status_code == 200
    assert "job-feedback--list" in home.text
    assert "job-feedback.js" in home.text
    assert "data-feedback-counts" not in home.text

    rated_block = re.search(
        rf'data-job-id="{re.escape(job_rated)}"[^>]*data-user-rating="1"',
        home.text,
    )
    assert rated_block, "rated job should show thumbs-up selection on the list"

    rated_li = re.search(
        rf'<li class="job-list-item">.*?{re.escape(job_rated)}.*?</li>',
        home.text,
        re.DOTALL,
    )
    assert rated_li
    assert rated_li.group(0).count("data-feedback-rating=") == 1
    assert "job-card-footer-actions" in rated_li.group(0)

    plain_li = re.search(
        rf'<li class="job-list-item">.*?{re.escape(job_plain)}.*?</li>',
        home.text,
        re.DOTALL,
    )
    assert plain_li
    assert "data-job-feedback" not in plain_li.group(0)
    assert "data-feedback-rating=" not in plain_li.group(0)


def test_jobs_list_api_shows_only_selected_feedback_icon(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _signup(client, caplog, "api-list-fb@example.com")
    job_down = _create_completed_job(client, url="https://www.youtube.com/watch?v=apidown")
    client.post(f"/api/jobs/{job_down}/feedback", json={"rating": -1})

    resp = client.get("/api/jobs/list")
    assert resp.status_code == 200
    html = resp.json()["html"]
    assert f'data-job-id="{job_down}"' in html
    assert 'data-user-rating="-1"' in html
    assert html.count(f'data-job-id="{job_down}"') == 1
    job_snippet = re.search(
        rf'<li class="job-list-item">.*?{re.escape(job_down)}.*?</li>',
        html,
        re.DOTALL,
    )
    assert job_snippet
    assert job_snippet.group(0).count("data-feedback-rating=") == 1
    assert 'data-feedback-rating="-1"' in job_snippet.group(0)
    assert 'data-feedback-rating="1"' not in job_snippet.group(0)


def test_home_job_list_hides_feedback_when_signed_out(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _signup(client, caplog, "list-out@example.com")
    _create_completed_job(client)
    client.post("/logout", follow_redirects=False)

    home = client.get("/")
    assert home.status_code == 200
    assert "job-feedback--list" not in home.text


def test_get_job_feedback_ratings_for_user_batch(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "batch.db")
    now = "2026-01-01T00:00:00+00:00"
    user = "user-a"
    ids = []
    for i in range(3):
        job_id = store.create_job(
            user_id=user,
            youtube_url=f"https://www.youtube.com/watch?v=batch{i}",
            reference_path="ref.wav",
            title=f"J{i}",
            description="",
            privacy="private",
            upload_enabled=False,
        )
        ids.append(job_id)
        with store._conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'completed', updated_at = ? WHERE id = ?",
                (now, job_id),
            )
    with store._conn() as conn:
        conn.execute(
            """
            INSERT INTO job_feedback (job_id, user_id, rating, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            """,
            (ids[0], user, now, now),
        )
        conn.execute(
            """
            INSERT INTO job_feedback (job_id, user_id, rating, created_at, updated_at)
            VALUES (?, ?, -1, ?, ?)
            """,
            (ids[2], user, now, now),
        )

    mapped = store.get_job_feedback_ratings_for_user(user, ids)
    assert mapped == {ids[0]: 1, ids[2]: -1}
    assert store.get_job_feedback_ratings_for_user("", ids) == {}


def test_delete_job_removes_feedback(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    from remastered.config import resolve_settings

    _signup(client, caplog, "deletefb@example.com")
    job_id = _create_completed_job(client)
    client.post(f"/api/jobs/{job_id}/feedback", json={"rating": -1})
    store = JobStore(resolve_settings().work_dir / "remastered_jobs.db")
    job = store.get_job(job_id)
    assert job is not None
    user_id = job["user_id"]
    assert store.get_job_feedback_rating(job_id, user_id) == -1
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    with store._conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM job_feedback WHERE job_id = ?", (job_id,)
        ).fetchone()
    assert row is None
