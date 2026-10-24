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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


_TINY_GIF = (
    "data:image/gif;base64,"
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


def _fake_youtube_meta(url: str) -> YouTubeVideoMeta:
    return YouTubeVideoMeta(
        video_id="dQw4w9WgXcQ",
        title="Never Gonna Give You Up",
        author_name="Rick Astley",
        thumbnail_url=_TINY_GIF,
        duration_seconds=212,
    )


@pytest.fixture(scope="module")
def preview_site(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    monkeypatch_module = MonkeyPatch()
    work = tmp_path_factory.mktemp("preview_thumb") / "work"
    refs = tmp_path_factory.mktemp("preview_thumb_refs")
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

    port = _free_port()
    config = uvicorn.Config(
        fastapi_application,
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
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


def test_empty_url_has_no_visible_preview_img(preview_site: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.add_init_script(
            "() => { try { localStorage.removeItem('remastered.reference'); } catch (e) {} }"
        )
        page.goto(preview_site + "/", wait_until="networkidle")
        page.wait_for_timeout(500)

        assert page.locator("#source-youtube-preview").is_hidden()
        assert page.locator("#source-yt-preview-img").count() == 1
        src = page.locator("#source-yt-preview-img").get_attribute("src")
        assert src is None or src == ""

        page.locator("#ref-tab-youtube").click()
        assert page.locator("#ref-youtube-preview").is_hidden()
        ref_src = page.locator("#ref-yt-preview-img").get_attribute("src")
        assert ref_src is None or ref_src == ""

        broken = page.evaluate(
            """() => {
              const imgs = document.querySelectorAll('#create-job-form img');
              for (const img of imgs) {
                if (!img.hasAttribute('hidden') && img.offsetParent !== null) {
                  if (!img.src || img.naturalWidth === 0) return true;
                }
              }
              return false;
            }"""
        )
        assert broken is False
        browser.close()


def test_preview_site_meta_api_is_mocked(preview_site: str) -> None:
    resp = httpx.get(
        preview_site + "/api/youtube/meta",
        params={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        timeout=5,
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Never Gonna Give You Up"


def test_loaded_meta_shows_thumbnail_without_broken_icon(preview_site: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.goto(preview_site + "/", wait_until="domcontentloaded")
        page.locator("#youtube_url").fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        page.dispatch_event("#youtube_url", "input")
        page.wait_for_function(
            """() => {
              const title = document.getElementById('source-yt-preview-title');
              return title && title.textContent.includes('Never Gonna Give You Up');
            }"""
        )
        page.wait_for_function(
            """() => {
              const img = document.getElementById('source-yt-preview-img');
              const src = img && img.getAttribute('src');
              return src && src.indexOf('data:image') === 0;
            }"""
        )
        broken = page.evaluate(
            """() => {
              const img = document.getElementById('source-yt-preview-img');
              if (!img || img.hidden) return false;
              return !img.getAttribute('src') || img.naturalWidth === 0;
            }"""
        )
        assert broken is False
        browser.close()


def test_clear_url_button_hides_preview_and_clears_field(preview_site: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.goto(preview_site + "/", wait_until="domcontentloaded")
        page.locator("#youtube_url").fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        page.dispatch_event("#youtube_url", "input")
        page.wait_for_function(
            """() => document.getElementById('youtube_url-clear') && !document.getElementById('youtube_url-clear').hidden"""
        )
        page.locator("#youtube_url-clear").click()
        assert page.locator("#youtube_url").input_value() == ""
        assert page.locator("#source-youtube-preview").is_hidden()
        assert page.locator("#youtube_url-clear").is_hidden()
        browser.close()
