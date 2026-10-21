from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from remastered.upload import upload_video


def test_upload_video_mocked(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 50)
    secrets = tmp_path / "client_secrets.json"
    secrets.write_text('{"installed":{}}', encoding="utf-8")
    token = tmp_path / "token.json"

    mock_youtube = MagicMock()
    mock_request = MagicMock()
    mock_request.next_chunk.return_value = (None, {"id": "NEWVIDEO1"})
    mock_youtube.videos.return_value.insert.return_value = mock_request

    with (
        patch("remastered.upload.get_youtube_service", return_value=mock_youtube),
        patch("remastered.upload.MediaFileUpload"),
    ):
        vid = upload_video(
            video,
            client_secrets=secrets,
            token_path=token,
            title="Remastered 테스트",
            privacy="private",
        )

    assert vid == "NEWVIDEO1"
    mock_youtube.videos.return_value.insert.assert_called_once()
