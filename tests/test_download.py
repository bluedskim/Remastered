from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from remastered.download import (
    DEFAULT_SOCKET_TIMEOUT,
    _coerce_single_video_info,
    base_ydl_opts,
    download_audio,
    inspect_youtube_source,
)
from remastered.download import DownloadResult


def test_base_ydl_opts_includes_noplaylist_and_socket_timeout() -> None:
    opts = base_ydl_opts(quiet=True)
    assert opts["noplaylist"] is True
    assert opts["socket_timeout"] == DEFAULT_SOCKET_TIMEOUT


def test_coerce_single_video_from_playlist_entries() -> None:
    info = {
        "_type": "playlist",
        "entries": [
            {"id": "first", "title": "First"},
            {"id": "second", "title": "Second"},
        ],
    }
    coerced = _coerce_single_video_info(info)
    assert coerced["id"] == "first"


def test_coerce_playlist_without_entries_fails_korean() -> None:
    with pytest.raises(RuntimeError, match="재생목록"):
        _coerce_single_video_info({"_type": "playlist", "entries": []})


def test_inspect_youtube_source_passes_noplaylist(tmp_path: Path) -> None:
    captured: list[dict] = []

    class FakeYDL:
        def __init__(self, opts: dict) -> None:
            captured.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            assert download is False
            assert "list=" not in url
            return {"id": "abc123", "title": "t", "duration": 3}

    vid = "Y0a0QuLEC8A"
    with patch("remastered.download.yt_dlp.YoutubeDL", FakeYDL):
        info = inspect_youtube_source(
            f"https://www.youtube.com/watch?v={vid}&list=RD{vid}&start_radio=1"
        )

    assert info["id"] == "abc123"
    assert captured[0]["noplaylist"] is True
    assert captured[0]["socket_timeout"] == DEFAULT_SOCKET_TIMEOUT


def test_download_audio_mocked(tmp_path: Path) -> None:
    fake_info = {
        "id": "abc123",
        "title": "테스트 곡",
        "thumbnail": "https://example.com/thumb.jpg",
    }
    captured: list[dict] = []

    class FakeYDL:
        def __init__(self, opts: dict) -> None:
            captured.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            assert download is True
            wav = tmp_path / "abc123.wav"
            wav.write_bytes(b"RIFF" + b"\x00" * 100)
            jpg = tmp_path / "abc123.jpg"
            jpg.write_bytes(b"\xff\xd8\xff")
            return fake_info

    with patch("remastered.download.yt_dlp.YoutubeDL", FakeYDL):
        result = download_audio("https://youtube.com/watch?v=abc123", tmp_path)

    assert isinstance(result, DownloadResult)
    assert result.video_id == "abc123"
    assert result.audio_path.suffix == ".wav"
    assert result.thumbnail_path is not None
    assert captured[0]["noplaylist"] is True
