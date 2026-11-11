from __future__ import annotations

from pathlib import Path

import pytest

from remastered.web.ab_loudness_match import (
    attenuation_db_to_linear,
    playback_attenuation_gains_db,
    playback_attenuation_gains_linear,
)
from remastered.web.app import PACKAGE_DIR

AB_LOUDNESS_JS = Path(PACKAGE_DIR) / "static" / "ab-loudness-match.js"
AB_PLAYER_JS = Path(PACKAGE_DIR) / "static" / "ab-player.js"


@pytest.mark.parametrize(
    ("lufs_original", "lufs_remastered", "expected"),
    [
        (-23.0, -17.0, (0.0, -6.0)),
        (-17.0, -23.0, (-6.0, 0.0)),
        (-18.5, -18.5, (0.0, 0.0)),
        (-30.0, -12.0, (0.0, -18.0)),
    ],
)
def test_playback_attenuation_only_lowers_louder_side(
    lufs_original: float,
    lufs_remastered: float,
    expected: tuple[float, float],
) -> None:
    assert playback_attenuation_gains_db(lufs_original, lufs_remastered) == expected


def test_playback_attenuation_never_boosts() -> None:
    orig_db, rem_db = playback_attenuation_gains_db(-20.0, -14.0)
    assert orig_db <= 0.0
    assert rem_db <= 0.0
    assert rem_db == pytest.approx(-6.0)
    lin_orig, lin_rem = playback_attenuation_gains_linear(-20.0, -14.0)
    assert lin_orig == pytest.approx(1.0)
    assert lin_rem == pytest.approx(10 ** (-6.0 / 20.0))


def test_playback_attenuation_non_finite_is_safe() -> None:
    assert playback_attenuation_gains_db(float("nan"), -14.0) == (0.0, 0.0)
    assert attenuation_db_to_linear(float("nan")) == 1.0
    assert attenuation_db_to_linear(3.0) == 1.0


def test_ab_loudness_match_js_exports_api() -> None:
    source = AB_LOUDNESS_JS.read_text(encoding="utf-8")
    assert "attenuationGainsDb" in source
    assert "integratedLoudnessLufs" in source
    assert "measureIntegratedLufsFromUrl" in source
    assert "RemasteredAbLoudnessMatch" in source


def test_ab_player_wires_loudness_match_toggle() -> None:
    source = AB_PLAYER_JS.read_text(encoding="utf-8")
    assert "[data-ab-loudness-match]" in source
    assert "loudnessMatchEnabled" in source
    assert "scheduleLoudnessMeasurement" in source
    assert "RemasteredAbLoudnessMatch" in source
    assert "LOUDNESS_MATCH_STORAGE_KEY" in source


def test_attenuation_db_to_linear() -> None:
    assert attenuation_db_to_linear(-6.0) == pytest.approx(10 ** (-6.0 / 20.0))
    assert attenuation_db_to_linear(0.0) == 1.0
    assert attenuation_db_to_linear(-80.0) == pytest.approx(10 ** (-80.0 / 20.0))
