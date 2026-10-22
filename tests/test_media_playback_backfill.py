from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from remastered.web.media import resolve_job_media_path


def test_resolve_inline_generates_playback_when_missing(tmp_path: Path) -> None:
    user_root = tmp_path / "users" / "u1"
    wav = user_root / "track.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"RIFF" + b"\x00" * 32)
    job = {"audio_path": str(wav)}

    with patch("remastered.web.media.transcode_wav_for_playback") as transcode:
        def _make_m4a(wav_path: Path) -> Path:
            out = wav_path.with_name(wav_path.stem + ".playback.m4a")
            out.write_bytes(b"fake")
            return out

        transcode.side_effect = _make_m4a
        resolved = resolve_job_media_path(
            job,
            "audio",
            work_dir=tmp_path,
            download=False,
        )
    transcode.assert_called_once()
    assert resolved.name == "track.playback.m4a"
