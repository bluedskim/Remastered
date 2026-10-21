from __future__ import annotations

from pathlib import Path

from remastered.web.stream_audio import playback_path_for_wav, transcode_wav_for_playback


def test_playback_path_for_wav() -> None:
    wav = Path("/work/users/u1/job/audio.wav")
    assert playback_path_for_wav(wav).name == "audio.playback.m4a"


def test_transcode_wav_for_playback(tmp_path: Path, monkeypatch) -> None:
    wav = tmp_path / "track.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)

    def fake_run(cmd, check, capture_output):  # noqa: ANN001
        out = Path(cmd[-1])
        out.write_bytes(b"fake-m4a")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr("remastered.web.stream_audio.subprocess.run", fake_run)
    out = transcode_wav_for_playback(wav)
    assert out == playback_path_for_wav(wav)
    assert out.is_file()
