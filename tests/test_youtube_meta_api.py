from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application
from remastered.web.youtube_meta import YouTubeVideoMeta, clear_youtube_meta_cache


@pytest.fixture
def meta_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    clear_youtube_meta_cache()
    with TestClient(fastapi_application) as client:
        yield client
    clear_youtube_meta_cache()


def test_youtube_meta_success(meta_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str) -> YouTubeVideoMeta:
        return YouTubeVideoMeta(
            video_id="abc123XYZ99",
            title="Perfect",
            author_name="Ed Sheeran",
            thumbnail_url="https://i.ytimg.com/vi/abc123XYZ99/hqdefault.jpg",
        )

    monkeypatch.setattr("remastered.web.app.fetch_youtube_video_meta", fake_fetch)
    resp = meta_client.get(
        "/api/youtube/meta",
        params={"url": "https://www.youtube.com/watch?v=abc123XYZ99"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["video_id"] == "abc123XYZ99"
    assert data["title"] == "Perfect"
    assert data["author_name"] == "Ed Sheeran"
    assert "hqdefault.jpg" in data["thumbnail_url"]


def test_youtube_meta_invalid_url(meta_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str) -> YouTubeVideoMeta:
        raise ValueError("Enter a valid YouTube reference URL.")

    monkeypatch.setattr("remastered.web.app.fetch_youtube_video_meta", fake_fetch)
    resp = meta_client.get("/api/youtube/meta", params={"url": "https://example.com/x"})
    assert resp.status_code == 400


def test_youtube_meta_oembed_failure(meta_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str) -> YouTubeVideoMeta:
        raise RuntimeError("Couldn't load video info.")

    monkeypatch.setattr("remastered.web.app.fetch_youtube_video_meta", fake_fetch)
    resp = meta_client.get(
        "/api/youtube/meta",
        params={"url": "https://www.youtube.com/watch?v=abc123XYZ99"},
    )
    assert resp.status_code == 502
    assert "Couldn't load video info" in resp.text
