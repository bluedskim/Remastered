from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from remastered.config import RemasteredSettings
from remastered.download import YOUTUBE_SCOPES


def get_youtube_oauth_status(settings: RemasteredSettings) -> dict[str, str]:
    if not settings.client_secrets or not settings.client_secrets.is_file():
        return {
            "status": "no_secrets",
            "label": "client_secrets 없음",
            "detail": "Google OAuth JSON을 /config/client_secrets.json 에 두세요.",
        }
    if not settings.token_path.is_file():
        return {
            "status": "no_token",
            "label": "로그인 필요",
            "detail": "아래 버튼으로 YouTube 계정을 연결하세요.",
        }
    try:
        creds = Credentials.from_authorized_user_file(
            str(settings.token_path), YOUTUBE_SCOPES
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "invalid",
            "label": "토큰 오류",
            "detail": str(exc),
        }
    if creds.valid:
        return {
            "status": "ok",
            "label": "연결됨",
            "detail": "YouTube 업로드에 사용할 수 있습니다.",
        }
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            settings.token_path.write_text(creds.to_json(), encoding="utf-8")
            return {
                "status": "ok",
                "label": "연결됨 (갱신됨)",
                "detail": "만료된 토큰을 자동 갱신했습니다.",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "refresh_failed",
                "label": "갱신 실패",
                "detail": str(exc),
            }
    return {
        "status": "expired",
        "label": "재로그인 필요",
        "detail": "토큰이 만료되었습니다. 다시 연결하세요.",
    }
