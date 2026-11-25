#!/usr/bin/env python3
"""Before/after spectrum Y-axis screenshots via real job-spectrum.js in headless Chromium."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path("/opt/cursor/artifacts")
HTML = ROOT / "scripts" / "spectrum_browser_screenshot.html"
BEFORE_JS = ROOT / "scripts" / "spectrum-yaxis-before.js"
AFTER_JS = ROOT / "scripts" / "spectrum-yaxis-after.js"


def _log_freqs(n: int = 100) -> list[float]:
    return [20 * (20000 / 20) ** (i / (n - 1)) for i in range(n)]


def realistic_job_spectrum_payload() -> dict:
    """
    Job-like fixture: ~80 Hz bass bump; original → ~−60 dB above 10 kHz;
    reference/remastered ~−20 dB between 10–16 kHz (visible above floor).
    """
    freqs = _log_freqs()
    o: list[float] = []
    r: list[float] = []
    m: list[float] = []
    for hz in freqs:
        bass = 9 * math.exp(-((math.log(hz) - math.log(80)) / 0.42) ** 2)
        if hz < 4000:
            orig = bass + 1.5 - 0.25 * max(0.0, math.log10(hz) - 2.2)
        elif hz < 10000:
            orig = 2.0 - 6.0 * (math.log10(hz) - 3.6)
        else:
            log_rel = max(0.0, math.log10(hz) - 4.0)
            orig = -22.0 - log_rel * 125.0
        if hz >= 8000:
            ref = -20.0 - 0.35 * max(0.0, math.log10(hz) - 3.85)
            mast = -19.0 - 0.4 * max(0.0, math.log10(hz) - 3.88)
        else:
            ref = bass * 0.45 + 0.5
            mast = bass * 0.5 + 0.8
        o.append(round(orig, 1))
        r.append(round(ref, 1))
        m.append(round(mast, 1))
    at_12k = min(
        payload_o
        for hz, payload_o in zip(freqs, o, strict=True)
        if hz >= 12_000
    )
    at_14k = [
        v
        for hz, v in zip(freqs, r, strict=True)
        if 10_000 <= hz <= 16_000
    ]
    assert at_12k <= -55, at_12k
    assert at_14k and -25 <= sum(at_14k) / len(at_14k) <= -15, at_14k
    return {"v": 1, "f": [round(f, 1) if f < 1000 else round(f) for f in freqs], "o": o, "r": r, "m": m}


def _write_payload_html(payload: dict, js_rel: str) -> Path:
    """Write a one-off HTML that inlines JSON and loads the chart script."""
    html = HTML.read_text(encoding="utf-8")
    html = html.replace(
        '<script type="application/json" class="spectrum-data" id="spectrum-payload"></script>',
        '<script type="application/json" class="spectrum-data" id="spectrum-payload">'
        + json.dumps(payload, separators=(",", ":"))
        + "</script>",
    )
    html = html.replace(
        """  <script id="chart-script"></script>
  <script>
    (function () {
      var params = new URLSearchParams(location.search);
      var src = params.get("js");
      if (!src) return;
      var s = document.getElementById("chart-script");
      s.src = src;
    })();
  </script>""",
        f'  <script src="{js_rel}"></script>',
    )
    tmp = ROOT / "scripts" / f".spectrum-shot-{Path(js_rel).name}.html"
    tmp.write_text(html, encoding="utf-8")
    return tmp


def _browser_screenshot(html_path: Path, out_png: Path) -> None:
    url = html_path.resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 820, "height": 520})
        page.goto(url, wait_until="networkidle")
        page.wait_for_selector(".spectrum-chart svg", timeout=10_000)
        panel = page.locator("[data-spectrum-panel]")
        panel.screenshot(path=str(out_png))
        browser.close()
    if not out_png.is_file() or out_png.stat().st_size < 1000:
        raise RuntimeError(f"screenshot missing or too small: {out_png}")


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    payload = realistic_job_spectrum_payload()
    if not BEFORE_JS.is_file():
        raise SystemExit("Run: git show main:src/remastered/web/static/job-spectrum.js > scripts/spectrum-yaxis-before.js")

    AFTER_JS.write_text(
        (ROOT / "src/remastered/web/static/job-spectrum.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    before_html = _write_payload_html(payload, "spectrum-yaxis-before.js")
    after_html = _write_payload_html(payload, "spectrum-yaxis-after.js")

    before_png = ARTIFACTS / "spectrum-yaxis-before.png"
    after_png = ARTIFACTS / "spectrum-yaxis-after.png"

    _browser_screenshot(before_html, before_png)
    _browser_screenshot(after_html, after_png)
    print("original min (treble)", min(payload["o"][-30:]))
    print(
        "ref avg 10-16k",
        round(
            sum(v for hz, v in zip(payload["f"], payload["r"]) if 10_000 <= hz <= 16_000)
            / max(1, sum(1 for hz in payload["f"] if 10_000 <= hz <= 16_000)),
            1,
        ),
    )
    print("wrote", before_png, after_png)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(exc, file=sys.stderr)
        raise