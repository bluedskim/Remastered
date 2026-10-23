from __future__ import annotations

import re
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from remastered.web.app import fastapi_application

LONG_EMAIL = "bluedskim@gmail.com"
SCREENSHOT_DIR = Path("/opt/cursor/artifacts/screenshots")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def header_site(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    mpatch = MonkeyPatch()
    work = tmp_path_factory.mktemp("header_layout") / "work"
    refs = tmp_path_factory.mktemp("header_layout_refs")
    work.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "ref.wav").write_bytes(b"x")

    mpatch.setenv("REMASTERED_WORK_DIR", str(work))
    mpatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    mpatch.setenv("REMASTERED_MAIL_MODE", "log")
    mpatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
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

    import logging

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

    client = httpx.Client(base_url=base)
    client.get("/")
    client.post("/auth/send-verification", data={"login_id": LONG_EMAIL})
    logger.removeHandler(handler)
    assert codes
    code = codes[-1]

    signup = client.post(
        "/signup",
        data={
            "login_id": LONG_EMAIL,
            "password": "password123",
            "password_confirm": "password123",
            "verification_code": code,
        },
        follow_redirects=False,
    )
    assert signup.status_code == 303

    cookies = [
        {
            "name": name,
            "value": value,
            "domain": "127.0.0.1",
            "path": "/",
        }
        for name, value in client.cookies.items()
    ]

    yield base, cookies

    server.should_exit = True
    mpatch.undo()
    thread.join(timeout=5)


def _assert_header_fits(page, width: int, logged_in: bool) -> None:
    fits = page.evaluate(
        """() => {
          const doc = document.documentElement;
          if (doc.scrollWidth > doc.clientWidth + 1) return false;
          const header = document.querySelector('.site-header');
          if (header && header.scrollWidth > header.clientWidth + 1) return false;
          return true;
        }"""
    )
    assert fits

    if logged_in:
        ok = page.evaluate(
            """() => {
              const btn = document.querySelector('.auth-logout-form button');
              if (!btn) return false;
              const r = btn.getBoundingClientRect();
              const vw = document.documentElement.clientWidth;
              return (
                r.left >= 0 &&
                r.right <= vw + 0.5 &&
                r.width >= 44 &&
                r.height >= 44
              );
            }"""
        )
        assert ok
        title = page.locator(".auth-email").get_attribute("title")
        assert title == LONG_EMAIL
    else:
        login = page.locator('a.auth-link[href="/login"]')
        signup = page.locator('a.auth-link-primary[href="/signup"]')
        assert login.is_visible()
        assert signup.is_visible()
        for el in (login, signup):
            box = el.bounding_box()
            assert box is not None
            assert box["x"] + box["width"] <= width + 0.5


@pytest.mark.parametrize("width", [360, 390, 412])
def test_logged_in_header_fits_viewport(header_site, width: int) -> None:
    base, cookies = header_site
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 800})
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(f"{base}/", wait_until="networkidle")
        _assert_header_fits(page, width, logged_in=True)
        page.screenshot(
            path=str(SCREENSHOT_DIR / f"mobile-header-logged-in-{width}.png"),
            full_page=False,
        )
        browser.close()


@pytest.mark.parametrize("width", [360, 390, 412])
def test_logged_out_header_fits_viewport(header_site, width: int) -> None:
    base, _cookies = header_site
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 800})
        page = context.new_page()
        page.goto(f"{base}/", wait_until="networkidle")
        _assert_header_fits(page, width, logged_in=False)
        page.screenshot(
            path=str(SCREENSHOT_DIR / f"mobile-header-logged-out-{width}.png"),
            full_page=False,
        )
        browser.close()
