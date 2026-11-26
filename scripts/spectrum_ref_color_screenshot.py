#!/usr/bin/env python3
"""Before/after Reference line color screenshots (18ff496f-like fixture)."""

from __future__ import annotations

from pathlib import Path

from spectrum_yaxis_screenshot import (
    _browser_screenshot,
    _write_payload_html,
    realistic_job_spectrum_payload,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path("/opt/cursor/artifacts")
BEFORE_JS = ROOT / "scripts" / "spectrum-ref-color-before.js"
AFTER_JS = ROOT / "scripts" / "spectrum-ref-color-after.js"


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    payload = realistic_job_spectrum_payload()
    AFTER_JS.write_text(
        (ROOT / "src/remastered/web/static/job-spectrum.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if not BEFORE_JS.is_file():
        raise SystemExit("Missing scripts/spectrum-ref-color-before.js (snapshot from main)")

    before_html = _write_payload_html(payload, "spectrum-ref-color-before.js")
    after_html = _write_payload_html(payload, "spectrum-ref-color-after.js")
    before_png = ARTIFACTS / "spectrum-ref-color-before.png"
    after_png = ARTIFACTS / "spectrum-ref-color-after.png"
    _browser_screenshot(before_html, before_png)
    _browser_screenshot(after_html, after_png)
    print("wrote", before_png, after_png)


if __name__ == "__main__":
    main()