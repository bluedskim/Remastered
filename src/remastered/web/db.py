from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

STAGES = ("download", "master", "render", "upload")
WEB_STAGES = ("download", "master")
STATUSES = ("pending", "running", "completed", "failed", "cancelled")
SCHEMA_VERSION = 4
LEGACY_MIGRATION_USER_ID = "__legacy_local__"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
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
            self._migrate_schema(conn)

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _job_columns(conn: sqlite3.Connection) -> set[str]:
        return {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                google_sub TEXT NOT NULL UNIQUE,
                email TEXT,
                name TEXT,
                picture TEXT,
                encrypted_token BLOB,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                message TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            """
        )
        if not self._table_exists(conn, "jobs"):
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
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                """
            )
        else:
            cols = self._job_columns(conn)
            if "user_id" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT")
            if "files_cleaned" not in cols:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN files_cleaned INTEGER NOT NULL DEFAULT 0"
                )
            cols = self._job_columns(conn)
            if "client_ip" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN client_ip TEXT")
            self._backfill_legacy_jobs(conn)

        self._backfill_job_titles_from_logs(conn)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_client_ip ON jobs(client_ip)")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _backfill_legacy_jobs(conn: sqlite3.Connection) -> None:
        """PR #3 이전 작업에 로컬 마이그레이션 사용자 ID를 부여합니다."""
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM jobs WHERE user_id IS NULL OR user_id = ''"
        ).fetchone()["c"]
        if not count:
            return
        now = _utc_now()
        conn.execute(
            """
            INSERT OR IGNORE INTO users (
                id, google_sub, email, name, picture, encrypted_token,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                LEGACY_MIGRATION_USER_ID,
                "legacy-local-migration",
                "legacy@local",
                "Legacy (pre-upgrade)",
                now,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE jobs SET user_id = ?
            WHERE user_id IS NULL OR user_id = ''
            """,
            (LEGACY_MIGRATION_USER_ID,),
        )

    @staticmethod
    def _backfill_job_titles_from_logs(conn: sqlite3.Connection) -> None:
        """완료된 작업의 로그에서 YouTube 제목을 복원합니다."""
        pattern = re.compile(r"^다운로드 완료:\s*(.+?)\s*$")
        rows = conn.execute(
            """
            SELECT id FROM jobs
            WHERE title IS NULL OR trim(title) = ''
            """
        ).fetchall()
        for row in rows:
            job_id = row["id"]
            logs = conn.execute(
                """
                SELECT message FROM job_logs
                WHERE job_id = ? ORDER BY id ASC
                """,
                (job_id,),
            ).fetchall()
            for log_row in logs:
                match = pattern.match((log_row["message"] or "").strip())
                if match:
                    title = match.group(1).strip()
                    if title:
                        conn.execute(
                            "UPDATE jobs SET title = ? WHERE id = ?",
                            (title, job_id),
                        )
                    break

    def upsert_user(
        self,
        *,
        google_sub: str,
        email: str | None,
        name: str | None,
        picture: str | None,
        encrypted_token: bytes,
    ) -> str:
        now = _utc_now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE google_sub = ?", (google_sub,)
            ).fetchone()
            if row:
                user_id = row["id"]
                conn.execute(
                    """
                    UPDATE users SET email = ?, name = ?, picture = ?,
                    encrypted_token = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (email, name, picture, encrypted_token, now, user_id),
                )
                return user_id
            user_id = uuid4().hex
            conn.execute(
                """
                INSERT INTO users (
                    id, google_sub, email, name, picture, encrypted_token,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, google_sub, email, name, picture, encrypted_token, now, now),
            )
            return user_id

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def update_user_token(self, user_id: str, encrypted_token: bytes) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET encrypted_token = ?, updated_at = ? WHERE id = ?",
                (encrypted_token, _utc_now(), user_id),
            )

    def clear_user_token(self, user_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET encrypted_token = NULL, updated_at = ? WHERE id = ?",
                (_utc_now(), user_id),
            )

    def create_job(
        self,
        *,
        user_id: str,
        youtube_url: str,
        reference_path: str,
        title: str | None,
        description: str,
        privacy: str,
        upload_enabled: bool,
        client_ip: str | None = None,
    ) -> str:
        job_id = uuid4().hex
        now = _utc_now()
        stage_state = {s: "pending" for s in WEB_STAGES}
        stage_state["render"] = "skipped"
        stage_state["upload"] = "skipped"
        upload_enabled = False
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, user_id, created_at, updated_at, status, stage, youtube_url,
                    reference_path, title, description, privacy, upload_enabled,
                    stage_state, client_ip
                ) VALUES (?, ?, ?, ?, 'pending', 'download', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    user_id,
                    now,
                    now,
                    youtube_url,
                    reference_path,
                    title,
                    description,
                    privacy,
                    1 if upload_enabled else 0,
                    json.dumps(stage_state),
                    client_ip,
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
            "title",
            "error",
            "source_video_id",
            "uploaded_video_id",
            "audio_path",
            "mastered_path",
            "video_path",
            "stage_state",
            "files_cleaned",
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

    def get_job(self, job_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        with self._conn() as conn:
            if user_id:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE id = ? AND user_id = ?",
                    (job_id, user_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs WHERE user_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, limit),
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

    def count_user_active_jobs(self, user_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM jobs
                WHERE user_id = ? AND status IN ('pending', 'running')
                """,
                (user_id,),
            ).fetchone()
        return int(row["c"])

    def count_user_jobs_today(self, user_id: str) -> int:
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM jobs
                WHERE user_id = ? AND created_at >= ?
                """,
                (user_id, start),
            ).fetchone()
        return int(row["c"])

    def count_ip_active_jobs(self, client_ip: str) -> int:
        if not client_ip or client_ip == "unknown":
            return 0
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM jobs
                WHERE client_ip = ? AND status IN ('pending', 'running')
                """,
                (client_ip,),
            ).fetchone()
        return int(row["c"])

    def find_recent_duplicate_job(
        self,
        user_id: str,
        youtube_url: str,
        reference_path: str,
        *,
        within_seconds: int = 30,
    ) -> str | None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=within_seconds)
        ).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id FROM jobs
                WHERE user_id = ? AND youtube_url = ? AND reference_path = ?
                  AND created_at >= ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, youtube_url.strip(), reference_path, cutoff),
            ).fetchone()
        return row["id"] if row else None

    def count_ip_jobs_today(self, client_ip: str) -> int:
        if not client_ip or client_ip == "unknown":
            return 0
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM jobs
                WHERE client_ip = ? AND created_at >= ?
                """,
                (client_ip, start),
            ).fetchone()
        return int(row["c"])

    def list_jobs_for_cleanup(self, created_before_iso: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status IN ('completed', 'failed')
                  AND files_cleaned = 0
                  AND created_at < ?
                """,
                (created_before_iso,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def mark_files_cleaned(self, job_id: str) -> None:
        self.update_job(job_id, files_cleaned=1)

    def reset_interrupted(self) -> None:
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
        data["files_cleaned"] = bool(data.get("files_cleaned", 0))
        return data
