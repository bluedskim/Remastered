from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from remastered.config import PrivacyStatus
from remastered.download import YOUTUBE_SCOPES

logger = logging.getLogger(__name__)


def get_youtube_service(
    client_secrets: Path,
    token_path: Path,
) -> Any:
    """OAuth 설치형 앱 흐름으로 YouTube API 클라이언트를 만듭니다."""
    if not client_secrets.is_file():
        raise FileNotFoundError(
            f"OAuth 클라이언트 비밀 파일이 없습니다: {client_secrets}"
        )
    token_path.parent.mkdir(parents=True, exist_ok=True)
    creds: Credentials | None = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets), YOUTUBE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info("OAuth 토큰 저장: %s", token_path)

    return build("youtube", "v3", credentials=creds)


def upload_video(
    video_path: Path,
    *,
    client_secrets: Path,
    token_path: Path,
    title: str,
    description: str = "",
    privacy: PrivacyStatus = "private",
    category_id: str = "10",
    tags: list[str] | None = None,
) -> str:
    """마스터 결과 MP4를 YouTube에 업로드하고 새 video ID를 반환합니다."""
    if not video_path.is_file():
        raise FileNotFoundError(f"업로드할 비디오 없음: {video_path}")

    youtube = get_youtube_service(client_secrets, token_path)
    body: dict[str, Any] = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    if tags:
        body["snippet"]["tags"] = tags

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("업로드 진행: %d%%", int(status.progress() * 100))

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"업로드 응답에 video id 없음: {response}")
    logger.info("업로드 완료: https://www.youtube.com/watch?v=%s", video_id)
    return video_id
