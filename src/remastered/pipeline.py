from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from remastered.config import RemasteredSettings
from remastered.download import download_audio
from remastered.master import run_matchering
from remastered.paths import ensure_dir, job_dir
from remastered.upload import upload_video
from remastered.video import generate_placeholder_image, render_still_video

logger = logging.getLogger(__name__)

LogCallback = Callable[[str], None]
StageCallback = Callable[[str], None]


@dataclass
class PipelineProgress:
    """파이프라인 단계별 진행·로그 콜백."""

    log: LogCallback = lambda _msg: None
    stage_start: StageCallback = lambda _stage: None
    stage_done: StageCallback = lambda _stage: None


def run_pipeline_progress(
    url: str,
    reference: Path,
    settings: RemasteredSettings,
    progress: PipelineProgress | None = None,
    *,
    title: str | None = None,
    description: str = "",
    privacy: str | None = None,
    cover_image: Path | None = None,
    skip_upload: bool = False,
) -> dict[str, Path | str]:
    """단계별 콜백이 있는 파이프라인 (웹 UI·작업 큐용)."""
    prog = progress or PipelineProgress()

    prog.stage_start("download")
    prog.log("YouTube 오디오 다운로드 중…")
    work = ensure_dir(settings.work_dir)
    dl = download_audio(url, job_dir(work, "download"), yt_dlp_path=settings.yt_dlp_path)
    job = job_dir(work, dl.video_id)
    prog.log(f"다운로드 완료: {dl.title}")
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

    prog.stage_start("render")
    prog.log("정지 이미지 MP4 렌더 중…")
    image = cover_image or dl.thumbnail_path
    if image is None or not image.is_file():
        image = generate_placeholder_image(job / "cover.jpg")
    video_path = job / f"{dl.video_id}_remastered.mp4"
    render_still_video(
        master_out.output_path,
        image,
        video_path,
        ffmpeg_path=settings.ffmpeg_path,
    )
    prog.log(f"비디오: {video_path.name}")
    prog.stage_done("render")

    result: dict[str, Path | str] = {
        "audio": dl.audio_path,
        "mastered": master_out.output_path,
        "video": video_path,
        "video_id": dl.video_id,
    }
    if master_out.preview_path:
        result["preview"] = master_out.preview_path

    if skip_upload:
        prog.stage_done("upload")
        return result

    if not settings.client_secrets:
        raise ValueError(
            "업로드하려면 client_secrets 경로가 필요합니다 "
            "(설정 파일 또는 REMASTERED_CLIENT_SECRETS)."
        )

    prog.stage_start("upload")
    prog.log("YouTube 업로드 중…")
    upload_title = title or f"{dl.title} (Remastered)"
    new_id = upload_video(
        video_path,
        client_secrets=settings.client_secrets,
        token_path=settings.token_path,
        title=upload_title,
        description=description,
        privacy=privacy or settings.default_privacy,
        category_id=settings.upload_category_id,
        oauth_local_port=settings.oauth_local_port,
        oauth_use_console=settings.oauth_use_console,
    )
    prog.log(f"업로드 완료: https://www.youtube.com/watch?v={new_id}")
    prog.stage_done("upload")
    result["uploaded_video_id"] = new_id
    return result


def run_pipeline(
    url: str,
    reference: Path,
    settings: RemasteredSettings,
    *,
    title: str | None = None,
    description: str = "",
    privacy: str | None = None,
    cover_image: Path | None = None,
    skip_upload: bool = False,
) -> dict[str, Path | str]:
    return run_pipeline_progress(
        url,
        reference,
        settings,
        title=title,
        description=description,
        privacy=privacy,
        cover_image=cover_image,
        skip_upload=skip_upload,
    )
