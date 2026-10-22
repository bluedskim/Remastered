from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from remastered.web.app import PACKAGE_DIR

AB_PLAYER_JS = Path(PACKAGE_DIR) / "static" / "ab-player.js"


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
    assert 'data-ab-repeat' in html
    assert "반복 재생 (R)" in html


def test_ab_player_js_does_not_cancel_play_on_waiting_or_stalled() -> None:
    source = AB_PLAYER_JS.read_text(encoding="utf-8")
    assert 'addEventListener("stalled"' not in source
    assert "waiting" in source
    assert "onAudioWaiting" in source
    assert "userPauseBoth" in source
    waiting_idx = source.index('addEventListener("waiting"')
    waiting_block = source[waiting_idx : waiting_idx + 200]
    assert "userPauseBoth" not in waiting_block
    assert "pauseBoth" not in source


def test_ab_player_js_plays_synchronously_in_gesture_handler() -> None:
    source = AB_PLAYER_JS.read_text(encoding="utf-8")
    assert "startPlaybackFromGesture" in source
    assert "waitCanPlay" not in source
    click_idx = source.index('playBtn.addEventListener("click"')
    click_block = source[click_idx : click_idx + 280]
    assert "startPlaybackFromGesture()" in click_block


def test_ab_player_js_repeat_loop_without_native_loop_attribute() -> None:
    source = AB_PLAYER_JS.read_text(encoding="utf-8")
    assert "restartBothFromStart" in source
    assert "checkLoopRestart" in source
    assert "REPEAT_STORAGE_KEY" in source
    assert "localStorage" in source
    assert 'loop="' not in source
    assert ".loop =" not in source
    assert ".loop=" not in source
    ended_idx = source.index("addEventListener(\"ended\"")
    ended_block = source[ended_idx : ended_idx + 120]
    assert "handleTrackEnded" in ended_block


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
