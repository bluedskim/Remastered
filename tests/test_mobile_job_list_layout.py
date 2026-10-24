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
from remastered.web.db import JobStore
from remastered.web.stream_audio import playback_path_for_wav
from remastered.youtube_url import normalize_youtube_url

LONG_FAIL_URL = (
    "https://www.youtube.com/watch?v=pyc5RAe0734&list=RDpyc5RAe0734&start_radio=1"
)
SCREENSHOT_DIR = Path("/opt/cursor/artifacts/screenshots")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def mobile_site(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    monkeypatch_module = MonkeyPatch()
    work = tmp_path_factory.mktemp("mobile_layout") / "work"
    refs = tmp_path_factory.mktemp("mobile_layout_refs")
    work.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "ref.wav").write_bytes(b"RIFF" + b"\x00" * 32)

    monkeypatch_module.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch_module.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch_module.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch_module, reference_path=refs / "ref.wav")

    def fake_transcode(wav: Path) -> Path:
        out = playback_path_for_wav(wav)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 256)
        return out
    monkeypatch_module.setattr(
        "remastered.web.worker.transcode_wav_for_playback", fake_transcode
    )
    monkeypatch_module.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 60, "filesize": 1000, "title": "Test"},
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

    client = httpx.Client(base_url=base, follow_redirects=True)
    client.get("/")
    create = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "reference_name": "ref.wav",
        },
    )
    assert create.status_code == 200
    job_id = str(create.url).rstrip("/").split("/")[-1]

    store = JobStore(work / "remastered_jobs.db")
    job = store.get_job(job_id)
    assert job is not None
    owner_id = job["user_id"]
    ref = str(refs / "ref.wav")

    fail_id = store.create_job(
        user_id=owner_id,
        youtube_url=normalize_youtube_url(LONG_FAIL_URL),
        reference_path=ref,
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    store.update_job(
        fail_id,
        status="failed",
        stage="download",
        error="Download failed for test",
    )

    long_id = store.create_job(
        user_id=owner_id,
        youtube_url="https://www.youtube.com/watch?v=abc123XYZ01",
        reference_path=ref,
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    store.update_job(
        long_id,
        status="completed",
        title=(
            "Yesterday (Remastered 2015) — extra words to force wrapping on narrow screens"
        ),
    )

    cookies = [
        {
            "name": name,
            "value": value,
            "domain": "127.0.0.1",
            "path": "/",
        }
        for name, value in client.cookies.items()
    ]

    yield base, cookies, fail_id

    server.should_exit = True
    monkeypatch_module.undo()
    thread.join(timeout=5)


@pytest.mark.parametrize("width", [360, 390, 412])
def test_home_job_list_fits_viewport(mobile_site, width: int) -> None:
    base, cookies, fail_id = mobile_site
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 900})
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(f"{base}/", wait_until="networkidle")

        layout_ok = page.evaluate(
            """() => {
              const doc = document.documentElement;
              if (doc.scrollWidth > doc.clientWidth + 1) return false;
              const list = document.querySelector('.job-list');
              if (list && list.scrollWidth > list.clientWidth + 1) return false;
              const cards = document.querySelectorAll('.job-card');
              for (const card of cards) {
                if (card.scrollWidth > card.clientWidth + 1) return false;
                if (!card.querySelector('.job-card-footer .status-pill')) return false;
                if (card.querySelector('.job-card-head')) return false;
              }
              const longTitle = document.querySelector('.job-card-title');
              const body = longTitle && longTitle.closest('.job-card-body');
              if (!longTitle || !body) return false;
              const pill = body.querySelector('.job-card-footer .status-pill');
              if (!pill) return false;
              const titleRow = longTitle.getBoundingClientRect();
              const pillRow = pill.getBoundingClientRect();
              if (pillRow.top < titleRow.bottom - 2) return false;
              return true;
            }"""
        )
        assert layout_ok

        page.screenshot(
            path=str(SCREENSHOT_DIR / f"mobile-home-jobs-{width}.png"),
            full_page=True,
        )

        page.goto(f"{base}/jobs/{fail_id}", wait_until="networkidle")
        detail_fits = page.evaluate(
            """() => {
              const doc = document.documentElement;
              return doc.scrollWidth <= doc.clientWidth + 1;
            }"""
        )
        assert detail_fits
        assert "YouTube video pyc5RAe0734" in page.content()
        page.screenshot(
            path=str(SCREENSHOT_DIR / f"mobile-job-detail-{width}.png"),
            full_page=True,
        )
        browser.close()
