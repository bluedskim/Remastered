from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from remastered.web.app import fastapi_application
from remastered.web.youtube_meta import YouTubeVideoMeta
from remastered.web.youtube_thumb_cache import thumb_cache_path



def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _fake_youtube_meta(url: str) -> YouTubeVideoMeta:
    return YouTubeVideoMeta(
        video_id="abc123XYZ99",
        title="Reference Preview Song",
        author_name="Test Artist",
        thumbnail_url="/media/thumb/abc123XYZ99.jpg",
        duration_seconds=180,
    )


@pytest.fixture(scope="module")
def preview_restore_site(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    monkeypatch_module = MonkeyPatch()
    work = tmp_path_factory.mktemp("preview_restore") / "work"
    refs = tmp_path_factory.mktemp("preview_restore_refs")
    work.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "ref.wav").write_bytes(b"RIFF")

    monkeypatch_module.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch_module.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch_module.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch_module.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    monkeypatch_module.setattr(
        "remastered.web.app.fetch_youtube_video_meta",
        _fake_youtube_meta,
    )
    thumb_bytes = httpx.get(
        "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
        timeout=20,
    ).content
    thumb_cache_path(work, "abc123XYZ99").parent.mkdir(parents=True, exist_ok=True)
    thumb_cache_path(work, "abc123XYZ99").write_bytes(thumb_bytes)

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            fastapi_application,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    for _ in range(80):
        try:
            if httpx.get(f"{base}/health", timeout=0.5).status_code == 200:
                break
        except Exception:
            time.sleep(0.05)
    else:
        pytest.fail("server did not start")

    yield base

    server.should_exit = True
    monkeypatch_module.undo()
    thread.join(timeout=5)


def _wait_ref_thumb_visible(page) -> None:
    page.wait_for_function(
        """() => {
          const img = document.getElementById('ref-yt-preview-img');
          return img && !img.hidden && img.src.includes('/media/thumb/') && img.naturalWidth > 0;
        }"""
    )


def test_localstorage_restore_shows_reference_thumb(preview_restore_site: str) -> None:
    url = "https://www.youtube.com/watch?v=abc123XYZ99"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.goto(preview_restore_site + "/", wait_until="networkidle")
        page.evaluate(
            """() => {
              localStorage.setItem(
                'remastered.reference',
                JSON.stringify({
                  tab: 'youtube',
                  youtubeUrl: 'https://www.youtube.com/watch?v=abc123XYZ99'
                })
              );
            }"""
        )
        page.reload(wait_until="networkidle")
        assert page.locator("#reference_youtube_url").input_value() == url
        _wait_ref_thumb_visible(page)
        browser.close()


def test_pageshow_restores_reference_thumb_after_bfcache(preview_restore_site: str) -> None:
    url = "https://www.youtube.com/watch?v=abc123XYZ99"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.goto(preview_restore_site + "/", wait_until="domcontentloaded")
        page.locator("#ref-tab-youtube").click()
        page.locator("#reference_youtube_url").fill(url)
        page.dispatch_event("#reference_youtube_url", "input")
        _wait_ref_thumb_visible(page)

        page.goto(preview_restore_site + "/privacy", wait_until="domcontentloaded")
        page.go_back(wait_until="domcontentloaded")

        page.evaluate(
            """() => {
              const img = document.getElementById('ref-yt-preview-img');
              if (img) {
                img.hidden = true;
              }
              window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
            }"""
        )
        _wait_ref_thumb_visible(page)
        browser.close()
