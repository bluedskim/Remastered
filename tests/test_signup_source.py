from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application
from remastered.web.db import JobStore
from remastered.web.signup_source import (
    SIGNUP_SOURCE_COOKIE,
    normalize_signup_source,
    signup_source_from_query_params,
)


@pytest.fixture
def auth_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
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
    with TestClient(fastapi_application) as client:
        yield client


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Twitter  ", "twitter"),
        ("bad source!", None),
        ("a" * 70, "a" * 64),
        ("valid_source-1", "valid_source-1"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_signup_source(raw: str | None, expected: str | None) -> None:
    assert normalize_signup_source(raw) == expected


def test_signup_source_from_query_params_prefers_utm_over_ref() -> None:
    assert signup_source_from_query_params(
        {"utm_source": "newsletter", "ref": "friend"}
    ) == "newsletter"


def test_signup_source_from_query_params_uses_ref_when_no_utm() -> None:
    assert signup_source_from_query_params({"ref": "ProductHunt"}) == "producthunt"


def test_signup_source_from_query_params_invalid_utm_does_not_fall_back_to_ref() -> None:
    assert (
        signup_source_from_query_params({"utm_source": "bad source!", "ref": "ok"})
        is None
    )


def _extract_code_from_logs(caplog: pytest.LogCaptureFixture) -> str:
    for record in caplog.records:
        match = re.search(r"Email verification code for .+: (\d{6})", record.message)
        if match:
            return match.group(1)
    raise AssertionError("verification code not found in logs")


def test_signup_stores_signup_source(
    auth_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    auth_client.get("/?utm_source=LaunchEmail")
    caplog.set_level("INFO")
    auth_client.post("/auth/send-verification", data={"login_id": "source@example.com"})
    code = _extract_code_from_logs(caplog)
    signup = auth_client.post(
        "/signup",
        data={
            "login_id": "source@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
            "signup_source": "launchEmail",
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303

    store = JobStore(auth_client.app.state.store.db_path)
    user = store.get_auth_user_by_login_id("source@example.com")
    assert user is not None
    assert user["signup_source"] == "launchemail"


def test_invalid_signup_source_does_not_block_signup(
    auth_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    auth_client.post("/auth/send-verification", data={"login_id": "plain@example.com"})
    code = _extract_code_from_logs(caplog)
    signup = auth_client.post(
        "/signup",
        data={
            "login_id": "plain@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
            "signup_source": "not valid!!",
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303
    store = JobStore(auth_client.app.state.store.db_path)
    user = store.get_auth_user_by_login_id("plain@example.com")
    assert user is not None
    assert user["signup_source"] is None


def test_first_touch_signup_source_not_overwritten(
    auth_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = auth_client.get("/?utm_source=first")
    assert first.cookies.get(SIGNUP_SOURCE_COOKIE) == "first"

    auth_client.get("/?utm_source=second")
    assert auth_client.cookies.get(SIGNUP_SOURCE_COOKIE) == "first"

    caplog.set_level("INFO")
    auth_client.post(
        "/auth/send-verification", data={"login_id": "touch@example.com"}
    )
    code = _extract_code_from_logs(caplog)
    auth_client.post(
        "/signup",
        data={
            "login_id": "touch@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    store = JobStore(auth_client.app.state.store.db_path)
    user = store.get_auth_user_by_login_id("touch@example.com")
    assert user is not None
    assert user["signup_source"] == "first"


def test_existing_user_login_does_not_set_signup_source(
    auth_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    auth_client.post("/auth/send-verification", data={"login_id": "again@example.com"})
    code = _extract_code_from_logs(caplog)
    auth_client.post(
        "/signup",
        data={
            "login_id": "again@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
            "signup_source": "initial",
        },
        follow_redirects=False,
    )

    auth_client.get("/?utm_source=login_campaign")
    login = auth_client.post(
        "/login",
        data={"login_id": "again@example.com", "password": "password123"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    store = JobStore(auth_client.app.state.store.db_path)
    user = store.get_auth_user_by_login_id("again@example.com")
    assert user is not None
    assert user["signup_source"] == "initial"


def test_signup_source_from_server_cookie_when_form_omitted(
    auth_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    auth_client.get("/?ref=partner_site")
    caplog.set_level("INFO")
    auth_client.post(
        "/auth/send-verification", data={"login_id": "cookie@example.com"}
    )
    code = _extract_code_from_logs(caplog)
    signup = auth_client.post(
        "/signup",
        data={
            "login_id": "cookie@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303
    store = JobStore(auth_client.app.state.store.db_path)
    user = store.get_auth_user_by_login_id("cookie@example.com")
    assert user is not None
    assert user["signup_source"] == "partner_site"


def test_auth_users_migration_adds_signup_source(tmp_path) -> None:
    db_path = tmp_path / "jobs.db"
    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn.executescript(
        """
        CREATE TABLE auth_users (
            id TEXT PRIMARY KEY,
            login_id TEXT NOT NULL UNIQUE,
            nickname TEXT,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO auth_users (
            id, login_id, nickname, password_hash, created_at, updated_at
        ) VALUES (?, ?, NULL, ?, ?, ?)
        """,
        ("u1", "legacy@example.com", "hash", now, now),
    )
    conn.commit()
    conn.close()

    store = JobStore(db_path)
    user = store.get_auth_user_by_login_id("legacy@example.com")
    assert user is not None
    assert "signup_source" in user
    assert user["signup_source"] is None

    store.create_auth_user(
        login_id="new@example.com", password_hash="hash2", signup_source="web"
    )
    new_user = store.get_auth_user_by_login_id("new@example.com")
    assert new_user is not None
    assert new_user["signup_source"] == "web"
