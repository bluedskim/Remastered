from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from remastered.download import DownloadResult


def install_fake_worker_pipeline(
    monkeypatch,
    *,
    reference_path: Path | None = None,
    resolve_reference_wav: Callable | None = None,
) -> None:
    def fake_download(url, settings, progress=None, on_downloaded=None):
        if progress:
            progress.stage_start("download")
            progress.stage_done("download")
        video_id = "sharedvid"
        if on_downloaded:
            on_downloaded("Test Track", video_id)
        dl_dir = settings.work_dir / "download"
        dl_dir.mkdir(parents=True, exist_ok=True)
        audio = dl_dir / f"{video_id}.wav"
        audio.write_bytes(b"RIFF" + b"\x00" * 100)
        return DownloadResult(video_id, "Test Track", audio, None, url)

    def fake_master(dl, reference, settings, progress=None, stereo_width=None):
        if progress:
            progress.stage_start("master")
            progress.stage_done("master")
        mastered = settings.work_dir / dl.video_id / "master" / "mastered_16bit.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=f=880:d=0.3",
                    "-af",
                    "volume=12dB",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    "-c:a",
                    "pcm_s16le",
                    str(mastered),
                ],
                check=True,
                capture_output=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            mastered.write_bytes(b"RIFF" + b"\x00" * 100)
        return {
            "audio": dl.audio_path,
            "mastered": mastered,
            "video_id": dl.video_id,
            "title": dl.title,
        }

    def default_resolve(job, settings, log=None):
        if reference_path and reference_path.is_file():
            return reference_path, None
        path = settings.work_dir / "ref.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF")
        return path, None

    def fake_copy_matchering_to_raw(mastered: Path) -> Path:
        raw = mastered.with_name(
            mastered.name.replace("mastered_", "mastered_matchering_", 1)
            if "mastered_" in mastered.name
            else "mastered_matchering" + mastered.suffix
        )
        shutil.copy2(mastered, raw)
        return raw

    def fake_apply_tp(raw: Path, out: Path, **kwargs):
        shutil.copy2(raw, out)
        return (0.0, -6.0, -6.0, 0.0)

    monkeypatch.setattr("remastered.web.worker.download_source_audio", fake_download)
    monkeypatch.setattr("remastered.web.worker.master_downloaded_audio", fake_master)
    monkeypatch.setattr(
        "remastered.web.worker.resolve_reference_wav",
        resolve_reference_wav or default_resolve,
    )
    monkeypatch.setattr(
        "remastered.web.worker.copy_matchering_to_raw",
        fake_copy_matchering_to_raw,
    )
    monkeypatch.setattr(
        "remastered.web.worker.apply_final_true_peak_protection",
        fake_apply_tp,
    )
    monkeypatch.setattr(
        "remastered.web.worker.verify_delivered_playback_peak",
        lambda *a, **k: None,
    )
