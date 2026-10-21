from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest


def write_sine_wav(path: Path, freq: float, duration: float = 3.0, sr: int = 44100) -> None:
    import math

    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sr * duration)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for i in range(n):
            t = i / sr
            sample = int(0.25 * 32767 * math.sin(2 * math.pi * freq * t))
            wf.writeframes(struct.pack("<hh", sample, sample))


@pytest.fixture
def tone_pair(tmp_path: Path) -> tuple[Path, Path]:
    target = tmp_path / "target.wav"
    reference = tmp_path / "reference.wav"
    write_sine_wav(target, 440.0)
    write_sine_wav(reference, 880.0, duration=2.0)
    return target, reference
