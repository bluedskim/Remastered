from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application
from remastered.web.auth_jwt import AUTH_COOKIE_NAME
from remastered.web.auth_validation import is_valid_nickname
from remastered.web.auth_service import AuthService
from remastered.web.db import JobStore


@pytest.fixture
def auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
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


def _extract_code_from_logs(caplog: pytest.LogCaptureFixture) -> str:
    for record in caplog.records:
        match = re.search(r"Email verification code for .+: (\d{6})", record.message)
        if match:
            return match.group(1)
    raise AssertionError("verification code not found in logs")


def test_nickname_validation() -> None:
    assert is_valid_nickname("ab")
    assert is_valid_nickname("한글닉")
    assert is_valid_nickname("user_12")
    assert not is_valid_nickname("a")
    assert not is_valid_nickname("toolongnickname1")


def test_signup_login_logout_flow(
    auth_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    send = auth_client.post(
        "/auth/send-verification",
        data={"login_id": "user@example.com"},
    )
    assert send.status_code == 200
    code = _extract_code_from_logs(caplog)

    signup = auth_client.post(
        "/signup",
        data={
            "login_id": "user@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "nickname": "테스터",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303
    assert AUTH_COOKIE_NAME in signup.cookies

    home = auth_client.get("/")
    assert "테스터" in home.text
    assert "Log out" in home.text

    logout = auth_client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert AUTH_COOKIE_NAME not in logout.cookies or not logout.cookies.get(AUTH_COOKIE_NAME)

    login = auth_client.post(
        "/login",
        data={"login_id": "user@example.com", "password": "password123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert AUTH_COOKIE_NAME in login.cookies


def test_claim_anon_jobs_on_signup(
    auth_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    create = auth_client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    assert create.status_code == 303
    job_url = create.headers["location"]

    caplog.set_level("INFO")
    auth_client.post("/auth/send-verification", data={"login_id": "claim@example.com"})
    code = _extract_code_from_logs(caplog)
    auth_client.post(
        "/signup",
        data={
            "login_id": "claim@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "nickname": "클레임",
            "verification_code": code,
        },
        follow_redirects=False,
    )

    panel = auth_client.get(job_url + "/panel")
    assert panel.status_code in (200, 286)


def test_verification_expiry_and_attempts(tmp_path: Path) -> None:
    from remastered.web.auth_password import hash_secret

    store = JobStore(tmp_path / "auth.db")
    service = AuthService(store)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    store.upsert_email_verification(
        email="exp@example.com",
        code_hash=hash_secret("123456"),
        expires_at=past,
        sent_at=past,
    )
    with pytest.raises(Exception) as exc:
        service.signup(
            login_id="exp@example.com",
            password="password123",
            nickname="만료",
            verification_code="123456",
            anon_id="anon-test",
        )
    from remastered.web.auth_service import AuthError

    assert isinstance(exc.value, AuthError)
    assert "expired" in exc.value.message.lower()

    store.upsert_email_verification(
        email="fail@example.com",
        code_hash=hash_secret("111111"),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        sent_at=datetime.now(timezone.utc).isoformat(),
    )
    for _ in range(4):
        with pytest.raises(AuthError):
            service.signup(
                login_id="fail@example.com",
                password="password123",
                nickname="실패",
                verification_code="000000",
                anon_id="anon-test",
            )
    with pytest.raises(AuthError) as locked:
        service.signup(
            login_id="fail@example.com",
            password="password123",
            nickname="실패",
            verification_code="000000",
            anon_id="anon-test",
        )
    assert "new verification code" in locked.value.message.lower()
