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

ARTIFACTS = Path("/opt/cursor/artifacts")


@pytest.fixture(scope="module")
def stereo_form_site(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    monkeypatch_module = MonkeyPatch()
    work = tmp_path_factory.mktemp("stereo_pw") / "work"
    refs = tmp_path_factory.mktemp("stereo_pw_refs")
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
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "Test"},
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
            if httpx.get(f"{base}/", timeout=0.5).status_code == 200:
                break
        except Exception:
            time.sleep(0.05)
    else:
        pytest.fail("server did not start")

    yield base

    server.should_exit = True
    monkeypatch_module.undo()
    thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_stereo_width_mobile_control_visible(stereo_form_site: str) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    shot_path = ARTIFACTS / "stereo-width-form-mobile.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.goto(stereo_form_site + "/", wait_until="networkidle")
        stereo = page.locator(".field-stereo-width")
        stereo.scroll_into_view_if_needed()
        assert page.locator('input[value="follow_reference"]').is_checked()
        assert not page.locator('input[value="preserve"]').is_checked()
        stereo.screenshot(path=str(shot_path))
        browser.close()
    assert shot_path.is_file()


def test_stereo_width_follows_checked_on_select(stereo_form_site: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(stereo_form_site + "/", wait_until="domcontentloaded")
        page.locator('label.stereo-width-option:has(input[value="preserve"])').click()
        assert page.locator('input[value="preserve"]').is_checked()
        page.locator('label.stereo-width-option:has(input[value="follow_reference"])').click()
        assert page.locator('input[value="follow_reference"]').is_checked()
        browser.close()
