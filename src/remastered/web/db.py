from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

STAGES = ("download", "master", "render", "upload")
STATUSES = ("pending", "running", "completed", "failed", "cancelled")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
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
                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    message TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id);
                """
            )

    def create_job(
        self,
        *,
        youtube_url: str,
        reference_path: str,
        title: str | None,
        description: str,
        privacy: str,
        upload_enabled: bool,
    ) -> str:
        job_id = uuid4().hex
        now = _utc_now()
        stage_state = {s: "pending" for s in STAGES}
        if not upload_enabled:
            stage_state["upload"] = "skipped"
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, created_at, updated_at, status, stage, youtube_url,
                    reference_path, title, description, privacy, upload_enabled,
                    stage_state
                ) VALUES (?, ?, ?, 'pending', 'download', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    now,
                    now,
                    youtube_url,
                    reference_path,
                    title,
                    description,
                    privacy,
                    1 if upload_enabled else 0,
                    json.dumps(stage_state),
                ),
            )
        return job_id

    def append_log(self, job_id: str, message: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO job_logs (job_id, created_at, message) VALUES (?, ?, ?)",
                (job_id, _utc_now(), message),
            )
            conn.execute(
                "UPDATE jobs SET updated_at = ? WHERE id = ?",
                (_utc_now(), job_id),
            )

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "stage",
            "error",
            "source_video_id",
            "uploaded_video_id",
            "audio_path",
            "mastered_path",
            "video_path",
            "stage_state",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        if "stage_state" in updates and not isinstance(updates["stage_state"], str):
            updates["stage_state"] = json.dumps(updates["stage_state"])
        cols = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [_utc_now(), job_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE jobs SET {cols}, updated_at = ? WHERE id = ?",
                values,
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_logs(self, job_id: str, limit: int = 500) -> list[dict[str, str]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT created_at, message FROM job_logs
                WHERE job_id = ? ORDER BY id ASC LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()
        return [{"created_at": r["created_at"], "message": r["message"]} for r in rows]

    def next_pending(self) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id FROM jobs WHERE status = 'pending'
                ORDER BY created_at ASC LIMIT 1
                """
            ).fetchone()
        return row["id"] if row else None

    def reset_interrupted(self) -> None:
        """컨테이너 재시작 시 running 작업을 pending으로 되돌립니다."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE jobs SET status = 'pending', error = NULL
                WHERE status = 'running'
                """
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["upload_enabled"] = bool(data["upload_enabled"])
        data["stage_state"] = json.loads(data["stage_state"] or "{}")
        return data
