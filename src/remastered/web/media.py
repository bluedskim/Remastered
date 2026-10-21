from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from remastered.web.stream_audio import playback_path_for_wav

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
    user_id: str,
    work_dir: Path,
    download: bool = False,
) -> Path:
    wav = _wav_path_for_kind(job, kind)
    if wav is None:
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    user_root = (work_dir / "users" / user_id).resolve()
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
        return playback
    return wav


def media_file_response(path: Path, kind: str, *, download: bool = False) -> FileResponse:
    if path.suffix.lower() in {".m4a", ".mp4"}:
        media_type = "audio/mp4"
    else:
        media_type = "audio/wav"
    disposition = "attachment" if download else "inline"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'{disposition}; filename="{path.name}"',
    }
    return FileResponse(path, media_type=media_type, headers=headers)


def media_exists(path_str: str | None, user_id: str, work_dir: Path) -> bool:
    if not path_str:
        return False
    try:
        user_root = (work_dir / "users" / user_id).resolve()
        path = Path(path_str).resolve()
        path.relative_to(user_root)
    except ValueError:
        return False
    return path.is_file()
