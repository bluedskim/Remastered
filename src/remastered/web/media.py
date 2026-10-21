from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

RETENTION_MESSAGE = (
    "오디오 파일이 없습니다. 보관 기간(기본 14일)이 지난 경우 삭제되었을 수 있습니다."
)


def resolve_job_media_path(
    job: dict,
    kind: str,
    *,
    user_id: str,
    work_dir: Path,
) -> Path:
    key = {"audio": "audio_path", "mastered": "mastered_path"}.get(kind)
    if not key or not job.get(key):
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    user_root = (work_dir / "users" / user_id).resolve()
    path = Path(job[key]).resolve()
    try:
        path.relative_to(user_root)
    except ValueError:
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=RETENTION_MESSAGE)
    return path


def media_file_response(path: Path, kind: str) -> FileResponse:
    media_type = "audio/wav"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers={"Accept-Ranges": "bytes"},
    )


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
