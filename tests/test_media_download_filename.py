from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from remastered.web.download_filenames import (
    MAX_DOWNLOAD_FILENAME_LEN,
    build_attachment_content_disposition,
    media_download_filename,
    sanitize_filename_stem,
)


def _job(**overrides) -> dict:
    base = {
        "id": "job-uuid-123",
        "title": "",
        "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "source_video_id": "",
    }
    base.update(overrides)
    return base


def test_korean_title_mastered_filename() -> None:
    job = _job(title="봄날")
    name = media_download_filename(job, Path("/x/out.wav"))
    assert name == "봄날 (Remastered).wav"
    disp = build_attachment_content_disposition(job, Path("/x/out.wav"))
    assert 'filename="dQw4w9WgXcQ (Remastered).wav"' in disp
    assert "filename*=UTF-8''" in disp
    assert unquote(disp.split("filename*=UTF-8''", 1)[1]) == name


def test_illegal_chars_stripped_from_title() -> None:
    job = _job(title='곡/이름\\테스트:bad*file?"<>|')
    name = media_download_filename(job, Path("/x/track.wav"))
    assert "/" not in name
    assert "\\" not in name
    assert ":" not in name
    assert name.endswith("(Remastered).wav")
    assert "곡이름테스트badfile" in name.replace(" ", "")


def test_missing_title_falls_back_to_video_id() -> None:
    job = _job(title="", source_video_id="")
    name = media_download_filename(job, Path("/x/m.wav"))
    assert name == "dQw4w9WgXcQ (Remastered).wav"


def test_sanitize_filename_stem_collapses_whitespace() -> None:
    assert sanitize_filename_stem("  hello   world  ") == "hello world"


def test_filename_length_capped() -> None:
    long_title = "가" * 200
    job = _job(title=long_title)
    name = media_download_filename(job, Path("/x/out.wav"))
    assert len(name) <= MAX_DOWNLOAD_FILENAME_LEN
    assert name.endswith("(Remastered).wav")
