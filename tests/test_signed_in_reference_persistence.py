from __future__ import annotations

import logging
import re
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from remastered.web.app import fastapi_application
from remastered.web.auth_jwt import AUTH_COOKIE_NAME


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _extract_code_from_logs(caplog: pytest.LogCaptureFixture) -> str:
    for record in caplog.records:
        match = re.search(r"Email verification code for .+: (\d{6})", record.message)
        if match:
            return match.group(1)
    raise AssertionError("verification code not found in logs")


@pytest.fixture
def signed_in_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"RIFF" + b"\x00" * 16)
    (refs / "Pop - Artist - Track.wav").write_bytes(b"RIFF" + b"\x00" * 16)
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    with TestClient(fastapi_application) as client:
        yield client


def _signup(client: TestClient, caplog: pytest.LogCaptureFixture, email: str) -> None:
    caplog.set_level("INFO")
    client.post("/auth/send-verification", data={"login_id": email})
    code = _extract_code_from_logs(caplog)
    resp = client.post(
        "/signup",
        data={
            "login_id": email,
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert AUTH_COOKIE_NAME in resp.cookies


def test_api_put_get_reference_prefs(
    signed_in_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _signup(signed_in_client, caplog, "prefs@example.com")
    put = signed_in_client.put(
        "/api/reference-prefs",
        json={
            "tab": "youtube",
            "youtubeUrl": "https://www.youtube.com/watch?v=abc123XYZ99",
        },
    )
    assert put.status_code == 200
    assert put.json()["tab"] == "youtube"
    put2 = signed_in_client.put(
        "/api/reference-prefs",
        json={
            "tab": "preset",
            "presetName": "Pop - Artist - Track.wav",
            "presetLibrary": "shared",
            "youtubeUrl": "https://www.youtube.com/watch?v=abc123XYZ99",
        },
    )
    assert put2.status_code == 200
    got = signed_in_client.get("/api/reference-prefs")
    assert got.status_code == 200
    body = got.json()
    assert body["youtubeUrl"] == "https://www.youtube.com/watch?v=abc123XYZ99"
    assert body["presetName"] == "Pop - Artist - Track.wav"
    assert body["tab"] == "preset"


def test_create_job_uses_saved_upload_without_reupload(
    signed_in_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _signup(signed_in_client, caplog, "upload@example.com")
    wav = b"RIFF" + b"\x00" * 32
    up = signed_in_client.post(
        "/api/reference-upload",
        files={"reference_upload": ("my-ref.wav", wav, "audio/wav")},
    )
    assert up.status_code == 200
    signed_in_client.put(
        "/api/reference-prefs",
        json={"tab": "upload", "uploadFilename": "my-ref.wav"},
    )
    create = signed_in_client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "reference_source": "upload",
            "reference_saved_upload": "my-ref.wav",
        },
        follow_redirects=False,
    )
    assert create.status_code == 303


@pytest.fixture(scope="module")
def signed_in_playwright_site(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    mpatch = MonkeyPatch()
    work = tmp_path_factory.mktemp("signed_ref") / "work"
    refs = tmp_path_factory.mktemp("signed_ref_refs")
    work.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "ref.wav").write_bytes(b"RIFF" + b"\x00" * 16)
    (refs / "Pop - Artist - Track.wav").write_bytes(b"RIFF" + b"\x00" * 16)

    mpatch.setenv("REMASTERED_WORK_DIR", str(work))
    mpatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    mpatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    mpatch.setenv("REMASTERED_MAIL_MODE", "log")
    mpatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )

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

    codes: list[str] = []

    class Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            match = re.search(
                r"Email verification code for .+: (\d{6})", record.getMessage()
            )
            if match:
                codes.append(match.group(1))

    logger = logging.getLogger("remastered.web.auth_mail")
    handler = Handler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    email = "signed-ref@example.com"
    client = httpx.Client(base_url=base)
    client.post("/auth/send-verification", data={"login_id": email})
    logger.removeHandler(handler)
    code = codes[-1]
    client.post(
        "/signup",
        data={
            "login_id": email,
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    cookies = [
        {"name": name, "value": value, "domain": "127.0.0.1", "path": "/"}
        for name, value in client.cookies.items()
    ]

    yield base, cookies, work

    server.should_exit = True
    mpatch.undo()
    thread.join(timeout=5)


def test_signed_in_restore_youtube_after_reload(signed_in_playwright_site) -> None:
    base, cookies, _work = signed_in_playwright_site
    url = "https://www.youtube.com/watch?v=abc123XYZ99"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 800})
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(base + "/", wait_until="networkidle")
        page.locator("#ref-tab-youtube").click()
        page.locator("#reference_youtube_url").fill(url)
        page.wait_for_timeout(400)
        page.reload(wait_until="networkidle")
        assert page.locator("#ref-tab-youtube").get_attribute("aria-selected") == "true"
        assert page.locator("#reference_youtube_url").input_value() == url
        browser.close()


def test_signed_in_restore_preset_after_reload(signed_in_playwright_site) -> None:
    base, cookies, _work = signed_in_playwright_site
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 800})
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(base + "/", wait_until="networkidle")
        page.locator("#ref-tab-preset").click()
        page.locator("#reference_preset_select").select_option("Pop - Artist - Track.wav")
        page.wait_for_timeout(400)
        page.reload(wait_until="networkidle")
        assert page.locator("#ref-tab-preset").get_attribute("aria-selected") == "true"
        assert (
            page.locator("#reference_preset_select").input_value()
            == "Pop - Artist - Track.wav"
        )
        browser.close()


def test_signed_in_tab_switch_keeps_all_reference_types(
    signed_in_playwright_site,
) -> None:
    base, cookies, _work = signed_in_playwright_site
    url = "https://www.youtube.com/watch?v=abc123XYZ99"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 800})
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(base + "/", wait_until="networkidle")
        page.locator("#ref-tab-youtube").click()
        page.locator("#reference_youtube_url").fill(url)
        page.wait_for_timeout(300)
        page.locator("#ref-tab-preset").click()
        page.locator("#reference_preset_select").select_option("Pop - Artist - Track.wav")
        page.wait_for_timeout(300)
        page.locator("#ref-tab-youtube").click()
        assert page.locator("#reference_youtube_url").input_value() == url
        page.locator("#ref-tab-preset").click()
        assert (
            page.locator("#reference_preset_select").input_value()
            == "Pop - Artist - Track.wav"
        )
        page.reload(wait_until="networkidle")
        page.locator("#ref-tab-youtube").click()
        assert page.locator("#reference_youtube_url").input_value() == url
        page.locator("#ref-tab-preset").click()
        assert (
            page.locator("#reference_preset_select").input_value()
            == "Pop - Artist - Track.wav"
        )
        browser.close()


def test_signed_in_restore_upload_after_reload(signed_in_playwright_site) -> None:
    base, cookies, work = signed_in_playwright_site
    wav_path = work / "users"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 800})
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(base + "/", wait_until="networkidle")
        page.locator("#ref-tab-upload").click()
        page.set_input_files(
            "#reference_upload",
            {
                "name": "saved-ref.wav",
                "mimeType": "audio/wav",
                "buffer": b"RIFF" + b"\x00" * 64,
            },
        )
        page.wait_for_function(
            """() => document.getElementById('ref-source-summary').textContent.includes('saved-ref.wav')"""
        )
        page.wait_for_timeout(500)
        page.reload(wait_until="networkidle")
        assert page.locator("#ref-tab-upload").get_attribute("aria-selected") == "true"
        assert "saved-ref.wav" in page.locator("#ref-source-summary").inner_text()
        browser.close()
