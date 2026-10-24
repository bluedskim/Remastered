from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application
from remastered.web.youtube_meta import clear_youtube_meta_cache
from remastered.web.youtube_thumb_cache import (
    THUMB_PLACEHOLDER_SVG,
    ensure_thumb_cached,
    thumb_cache_path,
    youtube_thumb_media_url,
)


@pytest.fixture
def thumb_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    clear_youtube_meta_cache()
    with TestClient(fastapi_application) as client:
        yield client
    clear_youtube_meta_cache()


def test_invalid_video_id_returns_404(thumb_client: TestClient) -> None:
    resp = thumb_client.get("/media/thumb/not-a-valid-id.jpg")
    assert resp.status_code == 404


def test_thumb_endpoint_caches_and_reuses_file(
    thumb_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    video_id = "dQw4w9WgXcQ"
    fake_jpeg = b"\xff\xd8\xff" + b"fake-jpeg-bytes" * 20

    def fake_download(vid: str) -> bytes | None:
        assert vid == video_id
        return fake_jpeg

    monkeypatch.setattr(
        "remastered.web.youtube_thumb_cache._download_thumb_bytes",
        fake_download,
    )

    url = youtube_thumb_media_url(video_id)
    first = thumb_client.get(url)
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("image/jpeg")
    assert "max-age=604800" in first.headers.get("cache-control", "")
    cache_file = thumb_cache_path(work, video_id)
    assert cache_file.is_file()

    call_count = {"n": 0}

    def counting_download(vid: str) -> bytes | None:
        call_count["n"] += 1
        return fake_jpeg

    monkeypatch.setattr(
        "remastered.web.youtube_thumb_cache._download_thumb_bytes",
        counting_download,
    )

    second = thumb_client.get(url)
    assert second.status_code == 200
    assert call_count["n"] == 0


def test_thumb_download_failure_returns_placeholder(
    thumb_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "remastered.web.youtube_thumb_cache._download_thumb_bytes",
        lambda _vid: None,
    )
    resp = thumb_client.get("/media/thumb/abc123XYZ99.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg")
    assert resp.content == THUMB_PLACEHOLDER_SVG


def test_youtube_meta_api_uses_local_thumb_url(
    thumb_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remastered.web.youtube_meta import YouTubeVideoMeta

    def fake_fetch(url: str) -> YouTubeVideoMeta:
        return YouTubeVideoMeta(
            video_id="abc123XYZ99",
            title="Test",
            author_name="Artist",
            thumbnail_url="/media/thumb/abc123XYZ99.jpg",
            duration_seconds=120,
        )

    monkeypatch.setattr("remastered.web.app.fetch_youtube_video_meta", fake_fetch)
    resp = thumb_client.get(
        "/api/youtube/meta",
        params={"url": "https://www.youtube.com/watch?v=abc123XYZ99"},
    )
    assert resp.status_code == 200
    assert resp.json()["thumbnail_url"] == "/media/thumb/abc123XYZ99.jpg"


def test_home_job_list_uses_media_thumb_url(thumb_client: TestClient) -> None:
    thumb_client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    home = thumb_client.get("/")
    assert home.status_code == 200
    assert "/media/thumb/dQw4w9WgXcQ.jpg" in home.text


def test_ensure_thumb_cached_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    monkeypatch.setattr(
        "remastered.web.youtube_thumb_cache._download_thumb_bytes",
        lambda _vid: b"\xff\xd8\xff" + b"x" * 200,
    )
    assert ensure_thumb_cached(work, "abc123XYZ99") is True
    assert thumb_cache_path(work, "abc123XYZ99").is_file()
