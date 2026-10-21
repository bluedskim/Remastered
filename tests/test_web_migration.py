from __future__ import annotations

import sqlite3
from pathlib import Path

from remastered.web.db import LEGACY_MIGRATION_USER_ID, SCHEMA_VERSION, JobStore


def _create_pr3_database(path: Path) -> None:
    """PR #3 멀티유저 이전 jobs 스키마 (user_id 없음)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
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
            stage_state TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            message TEXT NOT NULL
        );
        INSERT INTO jobs (
            id, created_at, updated_at, status, stage, youtube_url,
            reference_path, description, privacy, upload_enabled, stage_state
        ) VALUES (
            'job1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00',
            'completed', 'done', 'https://youtu.be/x', '/references/a.wav',
            '', 'private', 0, '{}'
        );
        """
    )
    conn.commit()
    conn.close()


def test_upgrade_pr3_database(tmp_path: Path) -> None:
    db_path = tmp_path / "remastered_jobs.db"
    _create_pr3_database(db_path)

    store = JobStore(db_path)

    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()

    assert version == SCHEMA_VERSION
    assert "user_id" in cols
    assert "files_cleaned" in cols
    assert "client_ip" in cols

    job = store.get_job("job1")
    assert job is not None
    assert job["user_id"] == LEGACY_MIGRATION_USER_ID
