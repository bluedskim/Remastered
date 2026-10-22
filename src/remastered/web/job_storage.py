from __future__ import annotations

from pathlib import Path


def job_media_storage_owner_id(job: dict, work_dir: Path) -> str:
    """Filesystem owner for job media (may differ from job.user_id after account claim)."""
    users_root = (work_dir / "users").resolve()
    for key in ("audio_path", "mastered_path"):
        path_str = job.get(key)
        if not path_str:
            continue
        try:
            rel = Path(path_str).resolve().relative_to(users_root)
            if rel.parts:
                return rel.parts[0]
        except ValueError:
            continue
    return str(job["user_id"])
