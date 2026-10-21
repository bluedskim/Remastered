from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from remastered.paths import ensure_dir

logger = logging.getLogger(__name__)


def render_still_video(
    audio_path: Path,
    image_path: Path,
    output_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    """마스터 오디오와 정지 이미지로 MP4를 만듭니다."""
    ensure_dir(output_path.parent)
    if not audio_path.is_file():
        raise FileNotFoundError(f"오디오 없음: {audio_path}")
    if not image_path.is_file():
        raise FileNotFoundError(f"이미지 없음: {image_path}")

    cmd = [
        ffmpeg_path,
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(output_path),
    ]
    logger.debug("ffmpeg 명령: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 실패 (code {proc.returncode}): {proc.stderr[-2000:]}"
        )
    logger.info("비디오 렌더 완료: %s", output_path)
    return output_path


def generate_placeholder_image(path: Path, size: tuple[int, int] = (1920, 1080)) -> Path:
    """ffmpeg로 단색 플레이스홀더 이미지를 생성합니다."""
    ensure_dir(path.parent)
    w, h = size
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x1a1a2e:s={w}x{h}:d=1",
        "-frames:v",
        "1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"플레이스홀더 이미지 생성 실패: {proc.stderr}")
    return path
