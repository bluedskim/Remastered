#!/usr/bin/env python3
"""Screenshots for Spectrum tone vs actual level modes (real job-spectrum.js)."""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from spectrum_yaxis_screenshot import _browser_screenshot, _write_payload_html

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path("/opt/cursor/artifacts")
JS = ROOT / "src" / "remastered/web/static/job-spectrum.js"
JS_COPY = ROOT / "scripts" / "spectrum-actual-level-chart.js"


def actual_level_fixture_payload() -> dict:
    from spectrum_yaxis_screenshot import realistic_job_spectrum_payload

    payload = realistic_job_spectrum_payload()
    payload["v"] = 2
    # Original at −23 LUFS; reference −2.3 dB quieter; remastered +6.7 dB louder.
    payload["lu"] = [-23.0, -25.3, -16.3]
    return payload


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    payload = actual_level_fixture_payload()
    JS_COPY.write_text(JS.read_text(encoding="utf-8"), encoding="utf-8")
    html = _write_payload_html(payload, "spectrum-actual-level-chart.js")
    tone_png = ARTIFACTS / "spectrum-actual-level-tone.png"
    actual_png = ARTIFACTS / "spectrum-actual-level-actual.png"

    url = html.resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 820, "height": 560})
        page.goto(url, wait_until="networkidle")
        page.wait_for_selector(".spectrum-chart svg", timeout=10_000)
        panel = page.locator("[data-spectrum-panel]")
        panel.screenshot(path=str(tone_png))
        page.locator('[data-spectrum-mode="actual"]').click()
        page.wait_for_timeout(150)
        panel.screenshot(path=str(actual_png))
        browser.close()

    for path in (tone_png, actual_png):
        if not path.is_file() or path.stat().st_size < 1000:
            raise RuntimeError(f"screenshot missing or too small: {path}")
    print("lu", payload["lu"])
    print("wrote", tone_png, actual_png)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(exc, file=sys.stderr)
        raise