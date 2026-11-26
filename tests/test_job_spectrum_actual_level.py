"""Spectrum JSON v2 level offsets, lazy recompute, and actual-mode Y-axis."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from remastered.web.ab_loudness_match import remaster_volume_percent_of_original
from remastered.web.job_spectrum import (
    SPECTRUM_JSON_VERSION,
    compute_spectrum_comparison,
    loudness_offset_db,
    resolve_spectrum_for_job,
    spectrum_has_level_data,
    spectrum_json_bytes,
)
from tests.test_job_spectrum_chart_axis import compute_y_axis_range, job_18ff496f_like_series


def test_loudness_offset_db() -> None:
    assert loudness_offset_db(-23.0) == 0.0
    assert loudness_offset_db(-16.3) == pytest.approx(6.7)
    assert loudness_offset_db(-25.3) == pytest.approx(-2.3)


def test_spectrum_has_level_data() -> None:
    assert not spectrum_has_level_data({"v": 1, "lu": [-23, -23, -23]})
    assert spectrum_has_level_data({"v": 2, "lu": [-23.0, -25.3, -16.3]})
    assert not spectrum_has_level_data({"v": 2, "lu": [-23, -23]})


def test_compute_includes_integrated_lufs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_job_spectrum import _pink, _write_stereo_wav

    sr = 48_000
    n = sr * 2
    import numpy as np

    rng = np.random.default_rng(4)
    sig = _pink(n, sr, rng)
    paths = tmp_path / "o.wav", tmp_path / "r.wav", tmp_path / "m.wav"
    for p in paths:
        _write_stereo_wav(p, sig, sig, sr)

    lufs_triple = [-23.0, -25.3, -16.3]
    calls = {"i": 0}

    def fake_lufs(_p: Path, **_: object) -> float:
        idx = calls["i"]
        calls["i"] += 1
        return lufs_triple[idx]

    monkeypatch.setattr(
        "remastered.web.job_spectrum.measure_integrated_lufs",
        fake_lufs,
    )
    payload = compute_spectrum_comparison(
        original_wav=paths[0],
        reference_wav=paths[1],
        mastered_wav=paths[2],
    )
    assert payload is not None
    assert payload["v"] == SPECTRUM_JSON_VERSION
    assert payload["lu"] == lufs_triple
    assert spectrum_json_bytes(payload) < 12_000


def test_resolve_recomputes_v1_without_lu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remastered.web.db import JobStore

    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)
    v1 = {
        "v": 1,
        "f": [100.0, 1000.0],
        "o": [0.0, 0.0],
        "r": [0.0, 0.0],
        "m": [0.0, 0.0],
    }
    job_id = store.create_job(
        user_id="u1",
        youtube_url="https://youtu.be/x",
        reference_path=str(tmp_path / "r.wav"),
        title="t",
        description="",
        privacy="private",
        upload_enabled=False,
    )
    store.update_job(
        job_id,
        status="completed",
        audio_path=str(tmp_path / "o.wav"),
        mastered_path=str(tmp_path / "m.wav"),
        spectrum_comparison_json=json.dumps(v1),
    )
    v2 = {**v1, "v": 2, "lu": [-23.0, -24.0, -16.0]}
    compute_calls: list[str] = []

    def fake_compute(**_: object) -> dict:
        compute_calls.append("compute")
        return v2

    monkeypatch.setattr(
        "remastered.web.job_spectrum.compute_spectrum_comparison",
        fake_compute,
    )
    monkeypatch.setattr(
        "remastered.web.job_spectrum.media_exists",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "remastered.web.job_spectrum.reference_wav_if_available",
        lambda *a, **k: tmp_path / "r.wav",
    )

    row = store.get_job(job_id)
    assert row is not None
    out = resolve_spectrum_for_job(
        row,
        store=store,
        work_dir=tmp_path,
    )
    assert compute_calls == ["compute"]
    assert out == v2
    refreshed = store.get_job(job_id)
    assert refreshed is not None
    stored = json.loads(refreshed["spectrum_comparison_json"])
    assert stored["v"] == 2
    assert stored["lu"] == [-23.0, -24.0, -16.0]


def test_actual_mode_axis_covers_louder_remastered_peak() -> None:
    """+6.7 dB remastered offset must expand yMax (same rules as tone mode)."""
    base = job_18ff496f_like_series()
    offset = loudness_offset_db(-16.3)
    shifted_m = [v + offset for v in base]
    all_db = base + base + shifted_m
    peak = max(all_db)
    y_min, y_max, step, _ = compute_y_axis_range(all_db)
    assert peak > 30.0
    assert y_max >= peak + 2.5
    assert step in (10, 20, 6)


def test_spectrum_lufs_delta_matches_volume_percent_within_0_3_db() -> None:
    """Integrated LUFS in spectrum JSON should match the N% volume line (±0.3 dB)."""
    lufs_original = -23.0
    lufs_mastered = -16.3
    delta_lufs = lufs_mastered - lufs_original
    percent = remaster_volume_percent_of_original(lufs_mastered, lufs_original)
    assert percent is not None
    implied_db = 20.0 * math.log10(percent / 100.0)
    assert abs(implied_db - delta_lufs) <= 0.3