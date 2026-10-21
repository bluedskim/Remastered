from __future__ import annotations

import logging
from pathlib import Path

from remastered.config import RemasteredSettings
from remastered.download import download_audio
from remastered.master import run_matchering
from remastered.paths import ensure_dir, job_dir
from remastered.upload import upload_video
from remastered.video import generate_placeholder_image, render_still_video

logger = logging.getLogger(__name__)


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
    work = ensure_dir(settings.work_dir)
    dl = download_audio(url, job_dir(work, "download"), yt_dlp_path=settings.yt_dlp_path)
    job = job_dir(work, dl.video_id)

    master_out = run_matchering(
        dl.audio_path,
        reference,
        job / "master",
        bit_depth=settings.bit_depth,
        preview=settings.preview,
        stem="mastered",
    )

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

    result: dict[str, Path | str] = {
        "audio": dl.audio_path,
        "mastered": master_out.output_path,
        "video": video_path,
        "video_id": dl.video_id,
    }
    if master_out.preview_path:
        result["preview"] = master_out.preview_path

    if skip_upload:
        return result

    if not settings.client_secrets:
        raise ValueError(
            "업로드하려면 client_secrets 경로가 필요합니다 "
            "(설정 파일 또는 REMASTERED_CLIENT_SECRETS)."
        )

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
    result["uploaded_video_id"] = new_id
    return result
