"""Spectrum chart line styling (colors, stroke order) in job-spectrum.js."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JOB_SPECTRUM_JS = REPO_ROOT / "src" / "remastered" / "web" / "static" / "job-spectrum.js"
STYLE_CSS = REPO_ROOT / "src" / "remastered" / "web" / "static" / "style.css"


def test_reference_stroke_is_cyan_and_thinner_than_remastered() -> None:
    js = JOB_SPECTRUM_JS.read_text(encoding="utf-8")
    assert 'reference: "#22d3ee"' in js
    assert "spectrum-line--reference" in js
    assert "spectrum-line--remastered" in js
    assert '"1.25"' in js
    assert '"2.85"' in js
    ref_idx = js.index('{ key: "r"')
    mast_idx = js.index('{ key: "m"')
    assert ref_idx < mast_idx


def test_reference_legend_swatch_cyan() -> None:
    css = STYLE_CSS.read_text(encoding="utf-8")
    assert ".spectrum-legend__swatch--reference" in css
    assert "#22d3ee" in css.split(".spectrum-legend__swatch--reference")[1].split("}")[0]