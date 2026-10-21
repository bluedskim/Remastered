from __future__ import annotations

import json

import pytest

from google.oauth2.credentials import Credentials

from remastered.web.crypto import encrypt_token
from remastered.web.db import JobStore
from remastered.web.scopes import WEB_SIGNIN_SCOPES


def test_web_scopes_exclude_youtube_upload() -> None:
    assert "https://www.googleapis.com/auth/youtube.upload" not in WEB_SIGNIN_SCOPES


def test_legacy_youtube_token_loads_without_crash(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("REMASTERED_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    store = JobStore(tmp_path / "jobs.db")
    token = {
        "token": "ya29.x",
        "refresh_token": "1//refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "sec",
        "scopes": [
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/youtube.upload",
        ],
    }
    user_id = store.upsert_user(
        google_sub="legacy",
        email="l@example.com",
        name="Legacy",
        picture=None,
        encrypted_token=encrypt_token(json.dumps(token)),
    )
    row = store.get_user(user_id)
    from remastered.web.crypto import decrypt_token

    creds = Credentials.from_authorized_user_info(
        json.loads(decrypt_token(row["encrypted_token"]))
    )
    assert creds is not None
    assert any("youtube.upload" in s for s in (creds.scopes or []))
