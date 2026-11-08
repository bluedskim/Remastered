from __future__ import annotations

import http.server
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

from remastered.web.app import PACKAGE_DIR

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not installed",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _make_sine_wav(path: Path, *, duration: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=f=440:d={duration}",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )


def _ab_player_html(*, original_src: str, remastered_src: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>AB player test</title>
</head>
<body>
  <div class="ab-compare" data-ab-compare>
    <div class="ab-listen-toggle">
      <button type="button" class="ab-listen-btn" data-ab-side="original">Original</button>
      <button type="button" class="ab-listen-btn" data-ab-side="remastered">Remastered</button>
    </div>
    <div class="ab-audio-host">
      <audio data-audio="original" preload="auto" src="{original_src}"></audio>
      <audio data-audio="remastered" preload="auto" src="{remastered_src}"></audio>
    </div>
    <div class="ab-transport">
      <button type="button" data-ab-play aria-label="Play">▶</button>
      <button type="button" data-ab-repeat aria-pressed="true" aria-label="Repeat">🔁</button>
      <input type="range" data-ab-seek min="0" max="1000" value="0" />
      <input type="range" data-ab-volume min="0" max="100" value="100" />
    </div>
  </div>
  <script src="/static/ab-player.js"></script>
</body>
</html>"""


@pytest.fixture(scope="module")
def ab_player_site(tmp_path_factory):
    root = tmp_path_factory.mktemp("ab_player_pw")
    media = root / "media"
    media.mkdir()
    _make_sine_wav(media / "orig_match.wav", duration=1.2)
    _make_sine_wav(media / "rem_match.wav", duration=1.2)
    _make_sine_wav(media / "rem_short.wav", duration=0.5)

    static_dir = Path(PACKAGE_DIR) / "static"
    (root / "index.html").write_text(
        _ab_player_html(
            original_src="/media/orig_match.wav",
            remastered_src="/media/rem_match.wav",
        ),
        encoding="utf-8",
    )
    (root / "index_mismatch.html").write_text(
        _ab_player_html(
            original_src="/media/orig_match.wav",
            remastered_src="/media/rem_short.wav",
        ),
        encoding="utf-8",
    )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def translate_path(self, path: str) -> str:
            if path.startswith("/static/"):
                rel = path[len("/static/") :]
                return str(static_dir / rel)
            if path.startswith("/media/"):
                rel = path[len("/media/") :]
                return str(media / rel)
            return super().translate_path(path)

        def log_message(self, format: str, *args) -> None:
            pass

    port = _free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    for _ in range(40):
        try:
            if httpx.get(f"{base}/index.html", timeout=0.5).status_code == 200:
                break
        except Exception:
            time.sleep(0.05)
    else:
        pytest.fail("ab player fixture server did not start")

    yield base, str(media)

    httpd.shutdown()
    thread.join(timeout=3)


def _loop_flags(page) -> dict:
    return page.evaluate(
        """() => {
      const o = document.querySelector('[data-audio="original"]');
      const r = document.querySelector('[data-audio="remastered"]');
      return {
        originalLoop: o.loop,
        remasteredLoop: r.loop,
        repeatPressed: document.querySelector('[data-ab-repeat]').getAttribute('aria-pressed'),
      };
    }"""
    )


def test_ab_repeat_default_and_native_loop_when_durations_match(ab_player_site) -> None:
    base, _media = ab_player_site
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script(
            "() => { try { localStorage.removeItem('remastered.abPlayer.repeat'); } catch (e) {} }"
        )
        page.goto(base + "/index.html", wait_until="networkidle")
        page.wait_for_function(
            """() => {
          const a = document.querySelector('[data-audio="original"]');
          return a && a.readyState >= 1 && isFinite(a.duration) && a.duration > 0;
        }"""
        )
        flags = _loop_flags(page)
        assert flags["repeatPressed"] == "true"
        assert flags["originalLoop"] is True
        assert flags["remasteredLoop"] is True
        browser.close()


def test_ab_repeat_off_disables_native_loop(ab_player_site) -> None:
    base, _media = ab_player_site
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/index.html", wait_until="networkidle")
        page.wait_for_function(
            "() => document.querySelector('[data-audio=\"original\"]')?.duration > 0"
        )
        page.locator("[data-ab-repeat]").click()
        flags = _loop_flags(page)
        assert flags["repeatPressed"] == "false"
        assert flags["originalLoop"] is False
        assert flags["remasteredLoop"] is False
        browser.close()


def test_ab_duration_mismatch_skips_native_loop(ab_player_site) -> None:
    base, _media = ab_player_site
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/index_mismatch.html", wait_until="networkidle")
        page.wait_for_function(
            "() => document.querySelector('[data-audio=\"remastered\"]')?.duration > 0"
        )
        flags = _loop_flags(page)
        assert flags["originalLoop"] is False
        assert flags["remasteredLoop"] is False
        browser.close()


def test_ab_play_seek_and_side_switch(ab_player_site) -> None:
    base, _media = ab_player_site
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/index.html", wait_until="networkidle")
        page.wait_for_function(
            "() => document.querySelector('[data-audio=\"original\"]')?.duration > 0"
        )
        page.locator("[data-ab-play]").click()
        page.wait_for_timeout(300)
        page.evaluate(
            """() => {
          const seek = document.querySelector('[data-ab-seek]');
          seek.value = '800';
          seek.dispatchEvent(new Event('input', { bubbles: true }));
          seek.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
        )
        page.locator('button[data-ab-side="original"]').click()
        times = page.evaluate(
            """() => {
          const o = document.querySelector('[data-audio="original"]');
          const r = document.querySelector('[data-audio="remastered"]');
          return { o: o.currentTime, r: r.currentTime };
        }"""
        )
        assert abs(times["o"] - times["r"]) < 0.15
        browser.close()


def test_ab_native_loop_restarts_without_ended_play_call(ab_player_site) -> None:
    """With matched durations + repeat on, browser native loop should wrap currentTime."""
    base, _media = ab_player_site
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/index.html", wait_until="networkidle")
        page.wait_for_function(
            "() => document.querySelector('[data-audio=\"original\"]')?.duration > 0"
        )
        page.locator("[data-ab-play]").click()
        page.wait_for_timeout(1500)
        state = page.evaluate(
            """() => {
          const a = document.querySelector('[data-audio="original"]');
          return { t: a.currentTime, paused: a.paused, loop: a.loop };
        }"""
        )
        assert state["loop"] is True
        assert state["paused"] is False
        assert state["t"] < 0.6
        browser.close()


def test_ab_user_pause_not_resumed_on_visibility_simulation(ab_player_site) -> None:
    """Page Visibility API simulation only — not a real OS screen lock."""
    base, _media = ab_player_site
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base + "/index.html", wait_until="networkidle")
        page.wait_for_function(
            "() => document.querySelector('[data-audio=\"original\"]')?.duration > 0"
        )
        page.locator("[data-ab-play]").click()
        page.wait_for_timeout(200)
        page.locator("[data-ab-play]").click()
        page.evaluate(
            """() => {
          Object.defineProperty(document, 'visibilityState', {
            configurable: true,
            get: () => 'visible',
          });
          document.dispatchEvent(new Event('visibilitychange'));
        }"""
        )
        paused = page.evaluate(
            """() => {
          const a = document.querySelector('[data-audio="original"]');
          return a.paused;
        }"""
        )
        assert paused is True
        browser.close()
