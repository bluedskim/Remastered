from __future__ import annotations

from pathlib import Path
from typing import Callable

from remastered.config import RemasteredSettings
from remastered.download import download_audio
from remastered.master import run_matchering
from remastered.paths import ensure_dir, job_dir
from remastered.pipeline import PipelineProgress


OnDownloaded = Callable[[str, str], None]


def run_web_master_pipeline(
    url: str,
    reference: Path,
    settings: RemasteredSettings,
    progress: PipelineProgress | None = None,
    on_downloaded: OnDownloaded | None = None,
) -> dict[str, Path | str]:
    """웹 작업: 다운로드 → 마스터링만 수행합니다."""
    prog = progress or PipelineProgress()

    prog.stage_start("download")
    prog.log("YouTube 오디오 다운로드 중…")
    work = ensure_dir(settings.work_dir)
    dl = download_audio(url, job_dir(work, "download"), yt_dlp_path=settings.yt_dlp_path)
    job = job_dir(work, dl.video_id)
    prog.log(f"다운로드 완료: {dl.title}")
    if on_downloaded:
        on_downloaded(dl.title, dl.video_id)
    prog.stage_done("download")

    prog.stage_start("master")
    prog.log("matchering 마스터링 중…")
    master_out = run_matchering(
        dl.audio_path,
        reference,
        job / "master",
        bit_depth=settings.bit_depth,
        preview=settings.preview,
        stem="mastered",
    )
    prog.log(f"마스터 WAV: {master_out.output_path.name}")
    prog.stage_done("master")

    result: dict[str, Path | str] = {
        "audio": dl.audio_path,
        "mastered": master_out.output_path,
        "video_id": dl.video_id,
        "title": dl.title,
    }
    if master_out.preview_path:
        result["preview"] = master_out.preview_path
    return result
