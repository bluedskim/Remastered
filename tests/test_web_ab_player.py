from __future__ import annotations

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from remastered.web.app import PACKAGE_DIR


@pytest.fixture
def jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PACKAGE_DIR / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )


def test_job_players_ab_compare_template(jinja_env: Environment) -> None:
    html = jinja_env.get_template("partials/job_players.html").render(
        job={"id": "job-abc"},
        audio_ok=True,
        mastered_ok=True,
        ab_compare_ok=True,
        audio_download_filename="t (Original).wav",
        mastered_download_filename="t (Remastered).wav",
        rights_notice_short="권리 안내",
    )
    assert 'data-ab-compare' in html
    assert "원본" in html
    assert "리마스터" in html
    assert 'data-audio="original"' in html
    assert 'data-audio="remastered"' in html
    assert 'preload="auto"' in html
    assert "controls" not in html.split("data-ab-compare")[1].split("ab-downloads")[0]
    assert "원본 다운로드" in html
    assert "권리 안내" in html


def test_job_players_single_when_only_mastered(jinja_env: Environment) -> None:
    html = jinja_env.get_template("partials/job_players.html").render(
        job={"id": "job-abc"},
        audio_ok=False,
        mastered_ok=True,
        ab_compare_ok=False,
        audio_download_filename=None,
        mastered_download_filename="t (Remastered).wav",
        rights_notice_short="권리",
    )
    assert "data-ab-compare" not in html
    assert "<audio controls" in html
