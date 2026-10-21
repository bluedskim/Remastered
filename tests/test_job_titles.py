from __future__ import annotations

import sqlite3
from pathlib import Path

from remastered.web.db import JobStore
from remastered.web.job_titles import job_display_title, parse_title_from_log_message


def test_job_display_title_pending() -> None:
    assert job_display_title({"status": "pending", "youtube_url": "https://youtu.be/x"}) == (
        "제목 확인 중…"
    )


def test_job_display_title_from_field() -> None:
    assert job_display_title({"status": "completed", "title": "Yesterday"}) == "Yesterday"


def test_parse_title_from_log_message() -> None:
    assert parse_title_from_log_message("다운로드 완료: Yesterday") == "Yesterday"


def test_title_backfill_on_db_open(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
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
            upload_enabled INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            source_video_id TEXT,
            uploaded_video_id TEXT,
            audio_path TEXT,
            mastered_path TEXT,
            video_path TEXT,
            stage_state TEXT NOT NULL DEFAULT '{}',
            files_cleaned INTEGER NOT NULL DEFAULT 0,
            client_ip TEXT
        );
        CREATE TABLE job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            message TEXT NOT NULL
        );
        INSERT INTO jobs (
            id, user_id, created_at, updated_at, status, stage, youtube_url,
            reference_path, description, privacy, upload_enabled, stage_state
        ) VALUES (
            'j1', 'u1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00',
            'completed', 'done', 'https://youtu.be/x', '/ref.wav',
            '', 'private', 0, '{}'
        );
        INSERT INTO job_logs (job_id, created_at, message)
        VALUES ('j1', '2026-01-01T00:00:00+00:00', '다운로드 완료: Backfill Song');
        """
    )
    conn.commit()
    conn.close()

    store = JobStore(db_path)
    job = store.get_job("j1")
    assert job is not None
    assert job["title"] == "Backfill Song"
