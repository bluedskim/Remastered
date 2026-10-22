from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from remastered.web.download_filenames import build_attachment_content_disposition
from remastered.web.job_storage import job_media_storage_owner_id
from remastered.web.stream_audio import playback_path_for_wav, transcode_wav_for_playback

logger = logging.getLogger(__name__)

RETENTION_MESSAGE = (
    "오디오 파일이 없습니다. 보관 기간(기본 14일)이 지난 경우 삭제되었을 수 있습니다."
)


def _wav_path_for_kind(job: dict, kind: str) -> Path | None:
    key = {"audio": "audio_path", "mastered": "mastered_path"}.get(kind)
    if not key or not job.get(key):
        return None
    return Path(job[key])


def resolve_job_media_path(
    job: dict,
    kind: str,
    *,
    work_dir: Path,
    download: bool = False,
) -> Path:
    wav = _wav_path_for_kind(job, kind)
    if wav is None:
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    storage_owner = job_media_storage_owner_id(job, work_dir)
    user_root = (work_dir / "users" / storage_owner).resolve()
    wav = wav.resolve()
    try:
        wav.relative_to(user_root)
    except ValueError:
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    if not wav.is_file():
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    if download:
        return wav
    playback = playback_path_for_wav(wav)
    if playback.is_file():
        try:
            if playback.stat().st_mtime >= wav.stat().st_mtime:
                return playback
        except OSError:
            return playback
    try:
        return transcode_wav_for_playback(wav)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Playback transcode failed for %s: %s", wav, exc)
        return wav


def media_file_response(
    path: Path,
    kind: str,
    *,
    download: bool = False,
    job: dict | None = None,
) -> FileResponse:
    if path.suffix.lower() in {".m4a", ".mp4"}:
        media_type = "audio/mp4"
    else:
        media_type = "audio/wav"
    if download and job is not None:
        disposition = build_attachment_content_disposition(job, kind, path)
    elif download:
        disposition = f'attachment; filename="{path.name}"'
    else:
        disposition = f'inline; filename="{path.name}"'
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": disposition,
    }
    return FileResponse(path, media_type=media_type, headers=headers)


def media_exists(path_str: str | None, job: dict, work_dir: Path) -> bool:
    if not path_str:
        return False
    try:
        storage_owner = job_media_storage_owner_id(job, work_dir)
        user_root = (work_dir / "users" / storage_owner).resolve()
        path = Path(path_str).resolve()
        path.relative_to(user_root)
    except ValueError:
        return False
    return path.is_file()
