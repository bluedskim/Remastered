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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def ref_persist_site(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    monkeypatch_module = MonkeyPatch()
    work = tmp_path_factory.mktemp("ref_persist") / "work"
    refs = tmp_path_factory.mktemp("ref_persist_refs")
    work.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "ref.wav").write_bytes(b"RIFF" + b"\x00" * 16)
    (refs / "Pop - Artist - Track.wav").write_bytes(b"RIFF" + b"\x00" * 16)

    monkeypatch_module.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch_module.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch_module.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch_module.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch_module.setenv(
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

    yield base

    server.should_exit = True
    monkeypatch_module.undo()
    thread.join(timeout=5)


def test_restore_youtube_reference_after_reload(ref_persist_site: str) -> None:
    url = "https://www.youtube.com/watch?v=abc123XYZ99"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 800})
        page = context.new_page()
        page.goto(ref_persist_site + "/", wait_until="networkidle")

        page.locator("#ref-tab-youtube").click()
        page.locator("#reference_youtube_url").fill(url)
        page.wait_for_function(
            """() => {
              const s = document.getElementById('ref-source-summary');
              return s && s.textContent.includes('abc123XYZ99');
            }"""
        )

        page.reload(wait_until="networkidle")

        assert page.locator("#ref-tab-youtube").get_attribute("aria-selected") == "true"
        assert page.locator("#reference_youtube_url").input_value() == url
        summary = page.locator("#ref-source-summary").inner_text()
        assert "abc123XYZ99" in summary
        assert page.locator("#youtube_url").input_value() == ""

        browser.close()


def test_restore_preset_after_reload(ref_persist_site: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 800})
        page = context.new_page()
        page.goto(ref_persist_site + "/", wait_until="networkidle")

        page.locator("#reference_preset_select").select_option("Pop - Artist - Track.wav")
        page.wait_for_function(
            """() => {
              const s = document.getElementById('ref-source-summary');
              return s && s.textContent.includes('Artist') && s.textContent.includes('preset');
            }"""
        )

        page.reload(wait_until="networkidle")

        assert page.locator("#ref-tab-preset").get_attribute("aria-selected") == "true"
        assert page.locator("#reference_preset_select").input_value() == "Pop - Artist - Track.wav"
        summary = page.locator("#ref-source-summary").inner_text()
        assert "Artist" in summary
        assert "(preset)" in summary

        browser.close()


def test_restore_upload_tab_without_file(ref_persist_site: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.goto(ref_persist_site + "/", wait_until="networkidle")
        page.locator("#ref-tab-upload").click()
        page.wait_for_function(
            """() => document.getElementById('ref-source-summary').textContent.includes('choose a file')"""
        )
        page.reload(wait_until="networkidle")
        assert page.locator("#ref-tab-upload").get_attribute("aria-selected") == "true"
        assert page.locator("#ref-source-summary").inner_text() == "Reference: choose a file"
        browser.close()


def test_ref_source_tabs_equal_height_at_390px(ref_persist_site: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.goto(ref_persist_site + "/", wait_until="networkidle")

        for tab_id in ("ref-tab-preset", "ref-tab-youtube", "ref-tab-upload"):
            page.locator(f"#{tab_id}").click()
            page.wait_for_function(
                f"""() => document.getElementById('{tab_id}')?.getAttribute('aria-selected') === 'true'"""
            )
            page.wait_for_function(
                """() => {
                  const sel = document.querySelector('.ref-source-tab[aria-selected="true"]');
                  if (!sel) return false;
                  const border = getComputedStyle(sel).borderLeftColor;
                  return border.includes('168') || border.includes('139') || border.includes('124');
                }"""
            )
            layout_ok = page.evaluate(
                """() => {
                  const tabs = Array.from(document.querySelectorAll('.ref-source-tab'));
                  const panel = document.querySelector('.ref-source-panels');
                  if (tabs.length !== 3 || !panel) return false;
                  const tops = tabs.map((t) => t.getBoundingClientRect().top);
                  const heights = tabs.map((t) => t.getBoundingClientRect().height);
                  const bottoms = tabs.map((t) => t.getBoundingClientRect().bottom);
                  const topSpan = Math.max(...tops) - Math.min(...tops);
                  const heightSpan = Math.max(...heights) - Math.min(...heights);
                  if (topSpan >= 1 || heightSpan >= 1 || heights[0] < 43) return false;
                  for (let i = 0; i < tabs.length - 1; i++) {
                    const a = tabs[i].getBoundingClientRect();
                    const b = tabs[i + 1].getBoundingClientRect();
                    if (Math.abs(a.right - b.left) > 1.5) return false;
                  }
                  const panelTop = panel.getBoundingClientRect().top;
                  const bottomSpan = Math.max(...bottoms) - Math.min(...bottoms);
                  if (bottomSpan >= 1.5) return false;
                  return Math.abs(Math.max(...bottoms) - panelTop) <= 1.5;
                }"""
            )
            assert layout_ok

            emphasis_ok = page.evaluate(
                """() => {
                  const sel = document.querySelector('.ref-source-tab[aria-selected="true"]');
                  if (!sel) return false;
                  const before = getComputedStyle(sel, '::before');
                  const beforeH = parseFloat(before.height) || 0;
                  const topBorderW = parseFloat(getComputedStyle(sel).borderTopWidth) || 0;
                  const noTopAccent = beforeH < 1 && topBorderW < 0.5;
                  const sideBorder = getComputedStyle(sel).borderLeftColor;
                  const hasPurpleSides =
                    sideBorder.includes('168') ||
                    sideBorder.includes('139') ||
                    sideBorder.includes('124');
                  const bottomW = parseFloat(getComputedStyle(sel).borderBottomWidth) || 0;
                  const noBottomSeam = bottomW < 0.5;
                  const inactive = document.querySelector('.ref-source-tab[aria-selected="false"]');
                  const inactiveColor = inactive ? getComputedStyle(inactive).color : '';
                  const activeColor = getComputedStyle(sel).color;
                  const fw = parseInt(getComputedStyle(sel).fontWeight, 10) || 0;
                  const contrast =
                    inactive && inactiveColor !== activeColor && fw >= 700;
                  return noTopAccent && hasPurpleSides && noBottomSeam && contrast;
                }"""
            )
            assert emphasis_ok

        browser.close()
