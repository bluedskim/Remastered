from __future__ import annotations

from pathlib import Path

from remastered.video import generate_placeholder_image, render_still_video
from conftest import write_sine_wav


def test_render_still_video(tmp_path: Path) -> None:
    audio = tmp_path / "a.wav"
    write_sine_wav(audio, 220.0, duration=1.0)
    image = generate_placeholder_image(tmp_path / "cover.jpg")
    out = tmp_path / "out.mp4"
    render_still_video(audio, image, out)
    assert out.is_file()
    assert out.stat().st_size > 500
