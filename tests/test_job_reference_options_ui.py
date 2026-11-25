from __future__ import annotations

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from remastered.web.app import PACKAGE_DIR
from remastered.web.job_ui import enrich_job_ui


@pytest.fixture
def jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PACKAGE_DIR / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )


def _job_with_defaults(**overrides: object) -> dict:
    base = {
        "id": "job-ui-1",
        "status": "completed",
        "stereo_width_mode": "follow_reference",
        "final_tp_protection": True,
        "keep_source_treble_above_reference_cutoff": True,
        "reference_genre": "Pop",
        "reference_label": "Example reference",
        "created_at": "2025-01-15T12:00:00+00:00",
    }
    base.update(overrides)
    return enrich_job_ui(base)


def test_job_reference_renders_option_bullet_list(jinja_env: Environment) -> None:
    job = _job_with_defaults()
    html = jinja_env.get_template("partials/job_reference.html").render(job=job)
    assert '<ul class="job-reference-options' in html
    assert html.count('<li class="job-reference-option">') == 3
    assert 'class="job-reference-option-name">Stereo width:</span>' in html
    assert "Open toward reference width (default)" in html
    assert 'class="job-reference-option-name">True-peak safety:</span>' in html
    assert "On (default)" in html
    assert "true-peak safety, default" not in html.lower()
    assert (
        'class="job-reference-option-name">Source treble above reference cutoff:</span>'
        in html
    )
    assert "On (keep source air above reference cutoff, default)" in html


def test_job_reference_non_default_option_values(jinja_env: Environment) -> None:
    job = _job_with_defaults(
        stereo_width_mode="preserve",
        final_tp_protection=False,
        keep_source_treble_above_reference_cutoff=False,
    )
    html = jinja_env.get_template("partials/job_reference.html").render(job=job)
    assert "Preserve original width" in html
    assert "Off (legacy matchered level)" in html
    assert "Off (match reference treble fully)" in html