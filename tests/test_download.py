from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from remastered.download import DownloadResult, download_audio


def test_download_audio_mocked(tmp_path: Path) -> None:
    fake_info = {
        "id": "abc123",
        "title": "테스트 곡",
        "thumbnail": "https://example.com/thumb.jpg",
    }

    class FakeYDL:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            wav = tmp_path / "abc123.wav"
            wav.write_bytes(b"RIFF" + b"\x00" * 100)
            jpg = tmp_path / "abc123.jpg"
            jpg.write_bytes(b"\xff\xd8\xff")
            return fake_info

    with patch("remastered.download.yt_dlp.YoutubeDL", return_value=FakeYDL()):
        result = download_audio("https://youtube.com/watch?v=abc123", tmp_path)

    assert isinstance(result, DownloadResult)
    assert result.video_id == "abc123"
    assert result.audio_path.suffix == ".wav"
    assert result.thumbnail_path is not None
