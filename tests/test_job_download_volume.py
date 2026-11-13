from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from remastered.web.ab_loudness_match import (
    format_remaster_volume_of_original_line,
    remaster_volume_percent_of_original,
)
from remastered.web.app import PACKAGE_DIR
from remastered.web.integrated_loudness import parse_integrated_lufs_from_ebur128_stderr


@pytest.fixture
def jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PACKAGE_DIR / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )


@pytest.mark.parametrize(
    ("lufs_mastered", "lufs_original", "expected"),
    [
        (-20.0, -23.0, 141),
        (-23.0, -23.0, 100),
        (-26.0, -23.0, 71),
        (-17.0, -23.0, 200),
    ],
)
def test_remaster_volume_percent_of_original(
    lufs_mastered: float, lufs_original: float, expected: int
) -> None:
    assert remaster_volume_percent_of_original(lufs_mastered, lufs_original) == expected


def test_remaster_volume_percent_non_finite() -> None:
    assert remaster_volume_percent_of_original(float("nan"), -23.0) is None


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (120, "Remastered is 120% of original volume"),
        (68, "Remastered is 68% of original volume"),
        (None, None),
    ],
)
def test_format_remaster_volume_of_original_line(
    percent: int | None, expected: str | None
) -> None:
    assert format_remaster_volume_of_original_line(percent) == expected


def test_parse_integrated_lufs_from_ebur128_stderr() -> None:
    stderr = """
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -18.3 LUFS
    Threshold: -28.3 LUFS
"""
    assert parse_integrated_lufs_from_ebur128_stderr(stderr) == pytest.approx(-18.3)


def test_job_players_shows_fixed_download_volume_line(jinja_env: Environment) -> None:
    line = "Remastered is 112% of original volume"
    html = jinja_env.get_template("partials/job_players.html").render(
        job={"id": "job-abc"},
        audio_ok=True,
        mastered_ok=True,
        ab_compare_ok=True,
        remaster_volume_of_original_line=line,
        mastered_download_filename="t (Remastered).wav",
        rights_notice_short="Rights notice",
    )
    assert 'data-ab-remaster-volume' in html
    assert line in html
    assert "data-ab-loudness-attenuation" not in html


def test_job_players_hides_download_volume_line_when_missing(jinja_env: Environment) -> None:
    html = jinja_env.get_template("partials/job_players.html").render(
        job={"id": "job-abc"},
        audio_ok=True,
        mastered_ok=True,
        ab_compare_ok=True,
        remaster_volume_of_original_line=None,
        mastered_download_filename="t (Remastered).wav",
        rights_notice_short="Rights notice",
    )
    assert "data-ab-remaster-volume" not in html
    assert "Remastered is" not in html
