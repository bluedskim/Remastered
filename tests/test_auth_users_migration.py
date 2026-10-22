from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from remastered.web.db import SCHEMA_VERSION, JobStore


def test_auth_users_migrates_legacy_nickname_constraint(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE auth_users (
            id TEXT PRIMARY KEY,
            login_id TEXT NOT NULL UNIQUE,
            nickname TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO auth_users (
            id, login_id, nickname, password_hash, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("u1", "legacy@example.com", "LegacyNick", "hash", now, now),
    )
    conn.commit()
    conn.close()

    store = JobStore(db_path)
    user = store.get_auth_user_by_login_id("legacy@example.com")
    assert user is not None
    assert user["nickname"] == "LegacyNick"

    store.create_auth_user(login_id="new@example.com", password_hash="hash2")
    new_user = store.get_auth_user_by_login_id("new@example.com")
    assert new_user is not None
    assert new_user["nickname"] is None

    conn = sqlite3.connect(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION
