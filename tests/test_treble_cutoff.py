from __future__ import annotations

import sqlite3
import wave
from pathlib import Path

import numpy as np
import pytest
from scipy import signal

from remastered.master import run_matchering
from remastered.treble_cutoff import (
    DEFAULT_TRANSITION_HZ,
    TrebleHoldPlan,
    apply_gain_hold_above_cutoff,
    detect_hard_reference_cutoff,
)
from remastered.web.db import SCHEMA_VERSION, JobStore


def _write_stereo_wav(path: Path, left: np.ndarray, right: np.ndarray, sr: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))), 1e-6)
    scale = 0.2 / peak
    li = np.clip(left * scale, -1.0, 1.0)
    ri = np.clip(right * scale, -1.0, 1.0)
    frames = np.stack([li, ri], axis=1)
    pcm = (frames * 32767.0).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _pink_noise(n: int, sr: int) -> np.ndarray:
    white = np.random.default_rng(42).standard_normal(n)
    b, a = signal.butter(1, 200.0 / (sr / 2), btype="high")
    return signal.filtfilt(b, a, white)


def _lowpass(sig: np.ndarray, sr: int, fc: float, order: int = 8) -> np.ndarray:
    wn = min(fc / (sr / 2), 0.999)
    b, a = signal.butter(order, wn, btype="low")
    return signal.filtfilt(b, a, sig)


def _natural_master_tilt(
    sig: np.ndarray,
    sr: int,
    db_per_khz: float = 2.0,
    knee_hz: float = 4_000.0,
) -> np.ndarray:
    """Slow roll-off typical of a naturally dark master (not a codec floor)."""
    n = len(sig)
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    out_db = np.where(
        freqs <= knee_hz,
        0.0,
        -db_per_khz * (freqs - knee_hz) / 1000.0,
    )
    gain = 10.0 ** (out_db / 20.0)
    gain[0] = 1.0
    return np.fft.irfft(spec * gain, n=n)


def _codec_style_gentle_cut(
    sig: np.ndarray,
    sr: int,
    knee_hz: float = 10_200.0,
    slope_db_per_khz: float = 6.3,
    floor_db: float = -55.0,
) -> np.ndarray:
    """Lossy-codec style: ~6 dB/kHz into a noise floor (YouTube-like)."""
    n = len(sig)
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    rolloff = -slope_db_per_khz * np.maximum(freqs - knee_hz, 0.0) / 1000.0
    out_db = np.maximum(rolloff, floor_db)
    gain = 10.0 ** (out_db / 20.0)
    return np.fft.irfft(spec * gain, n=n)


def _stereo_from_mono(mono: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return mono, mono


def _band_rms_db(path: Path, lo_hz: float, hi_hz: float, sr: int = 44100) -> float:
    with wave.open(str(path), "r") as wf:
        assert wf.getframerate() == sr
        raw = wf.readframes(wf.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2).astype(np.float64) / 32768.0
    mono = data.mean(axis=1)
    nperseg = min(8192, len(mono))
    freqs, psd = signal.welch(mono, fs=sr, nperseg=nperseg, noverlap=nperseg // 2)
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    power = float(np.sum(psd[band]))
    return 10.0 * np.log10(max(power, 1e-20))


def test_detects_steep_butterworth_lowpass() -> None:
    sr = 44100
    n = sr * 4
    mono = _lowpass(_pink_noise(n, sr), sr, 12_000.0)
    ref = np.column_stack(_stereo_from_mono(mono))
    fc = detect_hard_reference_cutoff(ref, sr)
    assert fc is not None
    assert 10_500 <= fc <= 13_500


def test_detects_gentle_codec_style_rolloff() -> None:
    sr = 44100
    n = sr * 8
    mono = _codec_style_gentle_cut(_pink_noise(n, sr), sr)
    ref = np.column_stack(_stereo_from_mono(mono))
    fc = detect_hard_reference_cutoff(ref, sr)
    assert fc is not None
    assert 10_000 <= fc <= 13_500


def test_natural_master_tilt_not_detected() -> None:
    sr = 44100
    n = sr * 8
    mono = _natural_master_tilt(_pink_noise(n, sr), sr, db_per_khz=2.0)
    ref = np.column_stack(_stereo_from_mono(mono))
    assert detect_hard_reference_cutoff(ref, sr) is None


def test_fullband_reference_not_detected() -> None:
    sr = 44100
    n = sr * 4
    mono = _pink_noise(n, sr)
    ref = np.column_stack(_stereo_from_mono(mono))
    assert detect_hard_reference_cutoff(ref, sr) is None


def test_gain_hold_flattens_above_cutoff() -> None:
    freqs = np.linspace(0, 22050, 2049)
    gain = 0.5 + 0.5 * np.sin(freqs / 3000.0)
    plan = TrebleHoldPlan(fc_hz=12_000.0, transition_hz=DEFAULT_TRANSITION_HZ)
    held = apply_gain_hold_above_cutoff(freqs, gain, plan)
    i_fc = int(np.searchsorted(freqs, 12_000.0))
    i_hold = int(np.searchsorted(freqs, 12_000.0 + DEFAULT_TRANSITION_HZ))
    assert np.std(held[i_hold:]) < 1e-6
    assert abs(held[i_hold] - held[i_fc - 10]) < 0.05


def test_option_off_bit_identical_to_stock_matchering(tmp_path: Path, tone_pair: tuple[Path, Path]) -> None:
    target, reference = tone_pair
    out_off = tmp_path / "off"
    out_on_nocut = tmp_path / "on"
    r_off = run_matchering(
        target,
        reference,
        out_off,
        keep_source_treble_above_reference_cutoff=False,
    )
    r_on = run_matchering(
        target,
        reference,
        out_on_nocut,
        keep_source_treble_above_reference_cutoff=True,
    )
    assert r_off.output_path.read_bytes() == r_on.output_path.read_bytes()


def test_migration_v16_to_v17_adds_treble_column(tmp_path: Path) -> None:
    db_path = tmp_path / "v16.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 16")
    conn.executescript(
        """
        CREATE TABLE users (id TEXT PRIMARY KEY, google_sub TEXT NOT NULL UNIQUE,
            email TEXT, name TEXT, picture TEXT, encrypted_token BLOB,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
            youtube_url TEXT NOT NULL, reference_path TEXT NOT NULL, title TEXT,
            description TEXT NOT NULL DEFAULT '', privacy TEXT NOT NULL DEFAULT 'private',
            upload_enabled INTEGER NOT NULL DEFAULT 0, error TEXT, source_video_id TEXT,
            uploaded_video_id TEXT, audio_path TEXT, mastered_path TEXT, video_path TEXT,
            stage_state TEXT NOT NULL DEFAULT '{}', files_cleaned INTEGER NOT NULL DEFAULT 0,
            client_ip TEXT, stereo_width_mode TEXT NOT NULL DEFAULT 'follow_reference',
            reference_source_type TEXT NOT NULL DEFAULT 'preset', reference_youtube_url TEXT,
            reference_video_id TEXT, reference_youtube_title TEXT,
            processing_stage TEXT NOT NULL DEFAULT 'queued',
            final_tp_protection INTEGER NOT NULL DEFAULT 1, memo TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
            created_at TEXT NOT NULL, message TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    store = JobStore(db_path)
    with store._conn() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert version == SCHEMA_VERSION == 17
    assert "keep_source_treble_above_reference_cutoff" in cols


def test_create_job_defaults_treble_hold_on(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)
    job_id = store.create_job(
        user_id="u1",
        youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        reference_path="shared/foo.wav",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    job = store.get_job(job_id)
    assert job is not None
    assert job.get("keep_source_treble_above_reference_cutoff") is True


def test_synthetic_e2e_preserves_air_above_reference_cutoff(tmp_path: Path) -> None:
    sr = 44100
    n = sr * 6
    rng = np.random.default_rng(7)
    source_mono = _pink_noise(n, sr)
    ref_mono = _lowpass(_pink_noise(n, sr), sr, 12_000.0)
    target = tmp_path / "target.wav"
    reference = tmp_path / "reference.wav"
    _write_stereo_wav(target, *_stereo_from_mono(source_mono), sr=sr)
    _write_stereo_wav(reference, *_stereo_from_mono(ref_mono), sr=sr)

    out_off = tmp_path / "out_off"
    out_on = tmp_path / "out_on"
    run_matchering(
        target,
        reference,
        out_off,
        keep_source_treble_above_reference_cutoff=False,
        stereo_width="preserve",
    )
    run_matchering(
        target,
        reference,
        out_on,
        keep_source_treble_above_reference_cutoff=True,
        stereo_width="preserve",
    )

    air_off = _band_rms_db(out_off / "mastered_16bit.wav", 12_000, 20_000, sr)
    air_on = _band_rms_db(out_on / "mastered_16bit.wav", 12_000, 20_000, sr)
    low_off = _band_rms_db(out_off / "mastered_16bit.wav", 200, 11_000, sr)
    low_on = _band_rms_db(out_on / "mastered_16bit.wav", 200, 11_000, sr)

    assert air_on > air_off + 3.0
    assert abs(low_on - low_off) <= 0.35

    pytest.synthetic_treble_report = {  # type: ignore[attr-defined]
        "band_12_20k_off_db": round(air_off, 2),
        "band_12_20k_on_db": round(air_on, 2),
        "band_below_11k_delta_db": round(low_on - low_off, 3),
    }