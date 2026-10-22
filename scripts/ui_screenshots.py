#!/usr/bin/env python3
"""Capture UI screenshots for the redesign."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

ARTIFACTS = Path("/opt/cursor/artifacts/screenshots")
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def _patch_pipeline() -> None:
    from remastered.web.stream_audio import playback_path_for_wav

    def fake_pipeline(url: str, reference: Path, settings, progress=None, on_downloaded=None):
        prog = progress
        if prog:
            prog.stage_start("download")
            prog.stage_done("download")
            prog.stage_start("master")
            prog.stage_done("master")
        if on_downloaded:
            on_downloaded("Screenshot Demo Track", "dQw4w9WgXcQ")
        audio = settings.work_dir / "demo" / "audio.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"RIFF" + b"\x00" * 200)
        mastered = settings.work_dir / "demo" / "master" / "out.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        mastered.write_bytes(b"RIFF" + b"\x00" * 200)
        for p in (audio, mastered):
            out = playback_path_for_wav(p)
            out.write_bytes(b"\x00" * 128)
        return {
            "audio": audio,
            "mastered": mastered,
            "video_id": "dQw4w9WgXcQ",
            "title": "Screenshot Demo Track",
        }

    import remastered.web.worker as worker_mod

    worker_mod.run_web_master_pipeline = fake_pipeline  # type: ignore[method-assign]

    import remastered.web.limits as limits_mod

    limits_mod.inspect_youtube_source = lambda url: {  # type: ignore[attr-defined]
        "duration": 120,
        "filesize": 1000,
    }


def _capture_set(context, base: str, label: str) -> None:
    for name, width in [("home-desktop", 1280), ("home-mobile", 390)]:
        page = context.new_page()
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(base, wait_until="networkidle")
        page.screenshot(
            path=str(ARTIFACTS / f"ui-{name}-{label}.png"),
            full_page=True,
        )
        page.close()

    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base, wait_until="networkidle")
    page.click("#create-job-submit")
    page.wait_for_url("**/jobs/**", timeout=20000)
    for _ in range(80):
        if page.locator("[data-ab-compare]").count() > 0:
            break
        time.sleep(0.25)
    page.screenshot(path=str(ARTIFACTS / f"ui-job-desktop-{label}.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 900})
    page.screenshot(path=str(ARTIFACTS / f"ui-job-mobile-{label}.png"), full_page=True)
    page.close()


def main() -> int:
    work = ROOT / ".screenshot-work"
    refs = work / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "Pop - NewJeans - Super Shy.wav").write_bytes(b"RIFF" + b"\x00" * 64)

    os.environ["REMASTERED_WORK_DIR"] = str(work)
    os.environ["REMASTERED_WEB_REFERENCES_DIR"] = str(refs)
    os.environ["REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS"] = "0"

    _patch_pipeline()

    import uvicorn

    config = uvicorn.Config(
        "remastered.web.app:fastapi_application",
        host="127.0.0.1",
        port=8765,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1.5)

    from playwright.sync_api import sync_playwright

    base = "http://127.0.0.1:8765"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("light", "dark"):
            context = browser.new_context(color_scheme=scheme)
            _capture_set(context, base, f"scheme-{scheme}")
            context.close()
        browser.close()

    # Legacy names (dark) for docs that reference older paths
    for stem in (
        "ui-home-desktop",
        "ui-home-mobile",
        "ui-job-desktop",
        "ui-job-mobile",
    ):
        src = ARTIFACTS / f"{stem}-scheme-dark.png"
        dst = ARTIFACTS / f"{stem}.png"
        if src.is_file():
            dst.write_bytes(src.read_bytes())

    server.should_exit = True
    thread.join(timeout=5)
    print("Saved screenshots to", ARTIFACTS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
