from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from remastered.config import resolve_settings
from remastered.web.crypto import decrypt_token, encrypt_token
from remastered.web.db import JobStore
from remastered.web.scopes import WEB_SIGNIN_SCOPES
from remastered.web.settings import WebSettings

logger = logging.getLogger(__name__)


def _google_client_credentials() -> tuple[str | None, str | None]:
    import os

    ws = WebSettings()
    cid = os.environ.get("REMASTERED_GOOGLE_CLIENT_ID") or ws.google_client_id
    secret = os.environ.get("REMASTERED_GOOGLE_CLIENT_SECRET") or ws.google_client_secret
    return cid, secret


def is_google_oauth_configured() -> bool:
    """웹 Google 로그인에 필요한 클라이언트 설정이 있는지 확인합니다."""
    client_id, client_secret = _google_client_credentials()
    if client_id and client_secret:
        return True
    settings = resolve_settings()
    if not settings.client_secrets or not settings.client_secrets.is_file():
        return False
    try:
        data = json.loads(settings.client_secrets.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return "web" in data or "installed" in data


def _load_oauth_client_config() -> dict[str, Any]:
    ws = WebSettings()
    client_id, client_secret = _google_client_credentials()
    if client_id and client_secret:
        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [ws.oauth_redirect_uri()],
            }
        }
    settings = resolve_settings()
    if settings.client_secrets and settings.client_secrets.is_file():
        data = json.loads(settings.client_secrets.read_text(encoding="utf-8"))
        if "web" in data:
            return data
        if "installed" in data:
            installed = data["installed"]
            return {
                "web": {
                    "client_id": installed["client_id"],
                    "client_secret": installed["client_secret"],
                    "auth_uri": installed.get(
                        "auth_uri", "https://accounts.google.com/o/oauth2/auth"
                    ),
                    "token_uri": installed.get(
                        "token_uri", "https://oauth2.googleapis.com/token"
                    ),
                    "redirect_uris": [WebSettings().oauth_redirect_uri()],
                }
            }
    raise FileNotFoundError(
        "Google OAuth is not configured. Set REMASTERED_GOOGLE_CLIENT_ID/SECRET or "
        "a web client_secrets.json."
    )


def make_oauth_flow(redirect_uri: str) -> Flow:
    config = _load_oauth_client_config()
    flow = Flow.from_client_config(config, scopes=WEB_SIGNIN_SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def authorization_url(redirect_uri: str) -> tuple[str, str]:
    flow = make_oauth_flow(redirect_uri)
    url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url, state


def exchange_code(authorization_response: str, redirect_uri: str) -> Credentials:
    flow = make_oauth_flow(redirect_uri)
    flow.fetch_token(authorization_response=authorization_response)
    return flow.credentials


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def upsert_user_from_credentials(store: JobStore, creds: Credentials) -> str:
    if not creds.token:
        raise ValueError("OAuth response did not include an access token.")
    info = fetch_userinfo(creds.token)
    google_sub = info.get("id") or info.get("sub")
    if not google_sub:
        raise ValueError("Could not determine Google user id.")
    encrypted = encrypt_token(creds.to_json())
    return store.upsert_user(
        google_sub=str(google_sub),
        email=info.get("email"),
        name=info.get("name"),
        picture=info.get("picture"),
        encrypted_token=encrypted,
    )


def get_user_credentials(store: JobStore, user_id: str) -> Credentials:
    row = store.get_user(user_id)
    if not row or not row.get("encrypted_token"):
        raise PermissionError("YouTube access is required. Please sign in again.")
    token_json = decrypt_token(row["encrypted_token"])
    # 기존 youtube.upload 토큰도 로드 가능 (스코프는 토큰 JSON에 포함)
    creds = Credentials.from_authorized_user_info(json.loads(token_json))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        store.update_user_token(user_id, encrypt_token(creds.to_json()))
    if not creds.valid:
        raise PermissionError("YouTube token expired. Please sign in again.")
    return creds


def revoke_user_token(store: JobStore, user_id: str) -> None:
    row = store.get_user(user_id)
    if not row or not row.get("encrypted_token"):
        store.clear_user_token(user_id)
        return
    try:
        token_json = decrypt_token(row["encrypted_token"])
        data = json.loads(token_json)
        token = data.get("refresh_token") or data.get("token")
        if token:
            with httpx.Client(timeout=30.0) as client:
                client.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("토큰 revoke 실패 (무시): %s", exc)
    store.clear_user_token(user_id)
