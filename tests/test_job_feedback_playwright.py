from __future__ import annotations

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
from remastered.web.video_download_access import reset_video_download_allowed_emails_cache

ARTIFACTS = Path("/opt/cursor/artifacts")


@pytest.fixture(scope="module")
def feedback_screenshots_site(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    monkeypatch_module = MonkeyPatch()
    work = tmp_path_factory.mktemp("fb_pw") / "work"
    refs = tmp_path_factory.mktemp("fb_pw_refs")
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
    monkeypatch_module.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch_module.setenv(
        "REMASTERED_WEB_VIDEO_DOWNLOAD_ALLOWED_EMAILS",
        "pw-admin@example.com",
    )
    reset_video_download_allowed_emails_cache()

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch_module, reference_path=refs / "ref.wav")
    monkeypatch_module.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 60, "filesize": 1000, "title": "PW Track"},
    )

    with TestClient(fastapi_application) as tc:
        import logging

        cap_records: list[logging.LogRecord] = []

        class _Cap(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                cap_records.append(record)

        h = _Cap()
        logging.getLogger().addHandler(h)
        logging.getLogger().setLevel(logging.INFO)
        tc.post("/auth/send-verification", data={"login_id": "pw-admin@example.com"})
        code = None
        for record in cap_records:
            match = re.search(
                r"Email verification code for .+: (\d{6})", record.getMessage()
            )
            if match:
                code = match.group(1)
        logging.getLogger().removeHandler(h)
        assert code
        signup = tc.post(
            "/signup",
            data={
                "login_id": "pw-admin@example.com",
                "password": "password123",
                "password_confirm": "password123",
                "verification_code": code,
            },
            follow_redirects=False,
        )
        assert signup.status_code == 303
        auth_cookie = tc.cookies.get("remastered_auth")
        assert auth_cookie

        tc.get("/")
        create = tc.post(
            "/jobs",
            data={
                "youtube_url": "https://www.youtube.com/watch?v=fbpw1",
                "reference_name": "ref.wav",
            },
            follow_redirects=False,
        )
        job_id = create.headers["location"].split("/")[-1]
        for _ in range(60):
            if tc.get(f"/jobs/{job_id}/panel").status_code == 286:
                break
            time.sleep(0.05)
        tc.post(f"/api/jobs/{job_id}/feedback", json={"rating": 1})

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

    cookies = [
        {
            "name": "remastered_auth",
            "value": auth_cookie,
            "url": base,
            "httpOnly": True,
            "sameSite": "Lax",
        }
    ]
    yield base, job_id, cookies

    server.should_exit = True
    monkeypatch_module.undo()
    reset_video_download_allowed_emails_cache()
    thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_job_feedback_screenshots(feedback_screenshots_site) -> None:
    base, job_id, cookies = feedback_screenshots_site
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    desktop = ARTIFACTS / "job-feedback-desktop.png"
    mobile = ARTIFACTS / "job-feedback-mobile.png"
    admin_shot = ARTIFACTS / "job-feedback-admin.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1100, "height": 900})
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
        fb = page.locator(".job-feedback")
        fb.scroll_into_view_if_needed()
        page.locator(".card-player").screenshot(path=str(desktop))

        mobile_ctx = browser.new_context(viewport={"width": 390, "height": 900})
        mobile_ctx.add_cookies(cookies)
        mobile_page = mobile_ctx.new_page()
        mobile_page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
        mobile_page.locator(".job-feedback").scroll_into_view_if_needed()
        mobile_page.locator(".job-feedback").screenshot(path=str(mobile))

        admin_ctx = browser.new_context(viewport={"width": 1100, "height": 800})
        admin_ctx.add_cookies(cookies)
        admin = admin_ctx.new_page()
        admin.goto(f"{base}/admin/feedback", wait_until="networkidle")
        admin.locator(".card").screenshot(path=str(admin_shot))
        browser.close()

    assert desktop.is_file() and mobile.is_file() and admin_shot.is_file()
