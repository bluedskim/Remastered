from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from remastered.stereo_width import DEFAULT_STEREO_WIDTH_MODE

STAGES = ("download", "master", "render", "upload")
WEB_STAGES = ("download", "master")
STATUSES = ("pending", "running", "completed", "failed", "cancelled")
SCHEMA_VERSION = 10
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
                    stereo_width_mode TEXT NOT NULL DEFAULT 'follow_reference',
                    reference_source_type TEXT NOT NULL DEFAULT 'preset',
                    reference_youtube_url TEXT,
                    reference_video_id TEXT,
                    reference_youtube_title TEXT,
                    processing_stage TEXT NOT NULL DEFAULT 'queued',
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
            cols = self._job_columns(conn)
            if "stereo_width_mode" not in cols:
                conn.execute(
                    """
                    ALTER TABLE jobs ADD COLUMN stereo_width_mode TEXT NOT NULL DEFAULT 'follow_reference'
                    """
                )
            cols = self._job_columns(conn)
            if "reference_source_type" not in cols:
                conn.execute(
                    """
                    ALTER TABLE jobs ADD COLUMN reference_source_type TEXT NOT NULL DEFAULT 'preset'
                    """
                )
            cols = self._job_columns(conn)
            if "reference_youtube_url" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN reference_youtube_url TEXT")
            cols = self._job_columns(conn)
            if "reference_video_id" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN reference_video_id TEXT")
            cols = self._job_columns(conn)
            if "reference_youtube_title" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN reference_youtube_title TEXT")
            cols = self._job_columns(conn)
            if "processing_stage" not in cols:
                conn.execute(
                    """
                    ALTER TABLE jobs ADD COLUMN processing_stage TEXT NOT NULL DEFAULT 'queued'
                    """
                )
            self._backfill_legacy_jobs(conn)

        self._backfill_job_titles_from_logs(conn)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_client_ip ON jobs(client_ip)")
        self._migrate_auth_schema(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _auth_users_needs_nickname_migration(conn: sqlite3.Connection) -> bool:
        if not JobStore._table_exists(conn, "auth_users"):
            return False
        nickname_notnull = False
        for row in conn.execute("PRAGMA table_info(auth_users)").fetchall():
            if row[1] == "nickname" and row[3]:
                nickname_notnull = True
        if nickname_notnull:
            return True
        index_sql = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND tbl_name = 'auth_users' AND sql IS NOT NULL
            """
        ).fetchall()
        for row in index_sql:
            sql = (row[0] or "").upper()
            if "NICKNAME" in sql and "UNIQUE" in sql:
                return True
        return False

    @staticmethod
    def _rebuild_auth_users_optional_nickname(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE auth_users_new (
                id TEXT PRIMARY KEY,
                login_id TEXT NOT NULL UNIQUE,
                nickname TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO auth_users_new (
                id, login_id, nickname, password_hash, created_at, updated_at
            )
            SELECT id, login_id, nickname, password_hash, created_at, updated_at
            FROM auth_users;
            DROP TABLE auth_users;
            ALTER TABLE auth_users_new RENAME TO auth_users;
            """
        )

    def _migrate_auth_schema(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "auth_users"):
            conn.execute(
                """
                CREATE TABLE auth_users (
                    id TEXT PRIMARY KEY,
                    login_id TEXT NOT NULL UNIQUE,
                    nickname TEXT,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        elif self._auth_users_needs_nickname_migration(conn):
            self._rebuild_auth_users_optional_nickname(conn)

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS email_verifications (
                email TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                failed_attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS verification_send_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                email TEXT NOT NULL,
                sent_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_verification_send_email
                ON verification_send_log(email, sent_at);
            CREATE INDEX IF NOT EXISTS idx_verification_send_ip
                ON verification_send_log(ip, sent_at);
            CREATE TABLE IF NOT EXISTS user_reference_prefs (
                user_id TEXT PRIMARY KEY,
                tab TEXT NOT NULL DEFAULT 'preset',
                preset_name TEXT NOT NULL DEFAULT '',
                preset_library TEXT NOT NULL DEFAULT 'shared',
                youtube_url TEXT NOT NULL DEFAULT '',
                upload_filename TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            """
        )

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
        pattern = re.compile(r"^(?:Download complete|다운로드 완료):\s*(.+?)\s*$")
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
        stereo_width_mode: str = DEFAULT_STEREO_WIDTH_MODE,
        reference_source_type: str = "preset",
        reference_youtube_url: str | None = None,
        reference_video_id: str | None = None,
        reference_youtube_title: str | None = None,
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
                    stage_state, client_ip, stereo_width_mode,
                    reference_source_type, reference_youtube_url, reference_video_id,
                    reference_youtube_title, processing_stage
                ) VALUES (?, ?, ?, ?, 'pending', 'download', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')
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
                    stereo_width_mode,
                    reference_source_type,
                    reference_youtube_url,
                    reference_video_id,
                    reference_youtube_title,
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
            "reference_youtube_title",
            "reference_path",
            "processing_stage",
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

    def get_job_for_access(
        self,
        job_id: str,
        *,
        account_id: str | None,
        anon_id: str,
    ) -> dict[str, Any] | None:
        owners = [anon_id]
        if account_id:
            owners.append(account_id)
        placeholders = ",".join("?" for _ in owners)
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT * FROM jobs WHERE id = ? AND user_id IN ({placeholders})",
                (job_id, *owners),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs_for_access(
        self,
        *,
        account_id: str | None,
        anon_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if account_id:
            return self.list_jobs(account_id, limit=limit)
        return self.list_jobs(anon_id, limit=limit)

    def claim_jobs(self, anon_id: str, account_id: str) -> int:
        if not anon_id or not account_id or anon_id == account_id:
            return 0
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE jobs SET user_id = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (account_id, _utc_now(), anon_id),
            )
        return int(cur.rowcount)

    def create_auth_user(
        self,
        *,
        login_id: str,
        password_hash: str,
    ) -> str:
        user_id = f"user-{uuid4().hex}"
        now = _utc_now()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO auth_users (
                    id, login_id, nickname, password_hash, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, ?)
                """,
                (user_id, login_id, password_hash, now, now),
            )
        return user_id

    def get_auth_user_by_login_id(self, login_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM auth_users WHERE login_id = ?", (login_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_auth_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM auth_users WHERE id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_email_verification(
        self,
        *,
        email: str,
        code_hash: str,
        expires_at: str,
        sent_at: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO email_verifications (
                    email, code_hash, expires_at, sent_at, failed_attempts
                ) VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(email) DO UPDATE SET
                    code_hash = excluded.code_hash,
                    expires_at = excluded.expires_at,
                    sent_at = excluded.sent_at,
                    failed_attempts = 0
                """,
                (email, code_hash, expires_at, sent_at),
            )

    def get_email_verification(self, email: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM email_verifications WHERE email = ?", (email,)
            ).fetchone()
        return dict(row) if row else None

    def delete_email_verification(self, email: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM email_verifications WHERE email = ?", (email,))

    def increment_verification_failed_attempts(self, email: str) -> int:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE email_verifications
                SET failed_attempts = failed_attempts + 1
                WHERE email = ?
                """,
                (email,),
            )
            row = conn.execute(
                "SELECT failed_attempts FROM email_verifications WHERE email = ?",
                (email,),
            ).fetchone()
        return int(row["failed_attempts"]) if row else 0

    def record_verification_send(self, *, ip: str, email: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO verification_send_log (ip, email, sent_at)
                VALUES (?, ?, ?)
                """,
                (ip, email, _utc_now()),
            )

    def count_verification_sends_since(
        self,
        *,
        email: str | None = None,
        ip: str | None = None,
        since_iso: str,
    ) -> int:
        with self._conn() as conn:
            if email is not None:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM verification_send_log
                    WHERE email = ? AND sent_at >= ?
                    """,
                    (email, since_iso),
                ).fetchone()
            elif ip is not None:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM verification_send_log
                    WHERE ip = ? AND sent_at >= ?
                    """,
                    (ip, since_iso),
                ).fetchone()
            else:
                return 0
        return int(row["c"])

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

    def delete_all_jobs_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE user_id = ?", (user_id,)
            ).fetchall()
            jobs = [self._row_to_job(r) for r in rows]
            if not jobs:
                return []
            job_ids = [j["id"] for j in jobs]
            placeholders = ",".join("?" for _ in job_ids)
            conn.execute(
                f"DELETE FROM job_logs WHERE job_id IN ({placeholders})",
                job_ids,
            )
            conn.execute(
                f"DELETE FROM jobs WHERE id IN ({placeholders})",
                job_ids,
            )
        return jobs

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
        return self.next_pending_fair(None)

    def count_pending_jobs(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE status = 'pending'"
            ).fetchone()
        return int(row["c"])

    def next_pending_fifo(self) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'pending'
                  AND user_id IS NOT NULL AND trim(user_id) != ''
                ORDER BY created_at ASC LIMIT 1
                """
            ).fetchone()
        return row["id"] if row else None

    def next_pending_fair(self, last_user_id: str | None) -> str | None:
        """Round-robin across owners: oldest pending job per owner, fair rotation."""
        with self._conn() as conn:
            owners = conn.execute(
                """
                SELECT user_id, MIN(created_at) AS oldest
                FROM jobs
                WHERE status = 'pending'
                  AND user_id IS NOT NULL AND trim(user_id) != ''
                GROUP BY user_id
                ORDER BY oldest ASC
                """
            ).fetchall()
            if owners:
                user_ids = [row["user_id"] for row in owners]
                if last_user_id and last_user_id in user_ids:
                    start = (user_ids.index(last_user_id) + 1) % len(user_ids)
                else:
                    start = 0
                for offset in range(len(user_ids)):
                    uid = user_ids[(start + offset) % len(user_ids)]
                    row = conn.execute(
                        """
                        SELECT id FROM jobs
                        WHERE status = 'pending' AND user_id = ?
                        ORDER BY created_at ASC LIMIT 1
                        """,
                        (uid,),
                    ).fetchone()
                    if row:
                        return row["id"]
        if self.count_pending_jobs() > 0:
            return self.next_pending_fifo()
        return None

    def queue_position(self, job_id: str) -> int:
        """1-based position among all pending jobs (by created_at)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT created_at, status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None or row["status"] != "pending":
                return 0
            ahead = conn.execute(
                """
                SELECT COUNT(*) AS c FROM jobs
                WHERE status = 'pending' AND created_at < ?
                """,
                (row["created_at"],),
            ).fetchone()
        return int(ahead["c"]) + 1

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

    def get_reference_prefs(self, user_id: str) -> dict[str, str] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_reference_prefs WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "tab": row["tab"],
            "preset_name": row["preset_name"],
            "preset_library": row["preset_library"],
            "youtube_url": row["youtube_url"],
            "upload_filename": row["upload_filename"],
        }

    def set_reference_prefs(
        self,
        user_id: str,
        *,
        tab: str,
        preset_name: str = "",
        preset_library: str = "shared",
        youtube_url: str = "",
        upload_filename: str = "",
    ) -> None:
        now = _utc_now()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO user_reference_prefs (
                    user_id, tab, preset_name, preset_library,
                    youtube_url, upload_filename, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    tab = excluded.tab,
                    preset_name = excluded.preset_name,
                    preset_library = excluded.preset_library,
                    youtube_url = excluded.youtube_url,
                    upload_filename = excluded.upload_filename,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    tab,
                    preset_name,
                    preset_library,
                    youtube_url,
                    upload_filename,
                    now,
                ),
            )

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
        self.requeue_orphaned_running_jobs(
            "Job re-queued after the server restarted while it was running."
        )

    def requeue_orphaned_running_jobs(self, message: str) -> int:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status = 'running'"
            ).fetchall()
        requeued = 0
        for row in rows:
            job_id = row["id"]
            self.update_job(job_id, status="pending", error=None)
            self.append_log(job_id, message)
            requeued += 1
        return requeued

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["upload_enabled"] = bool(data["upload_enabled"])
        data["stage_state"] = json.loads(data["stage_state"] or "{}")
        data["files_cleaned"] = bool(data.get("files_cleaned", 0))
        return data
