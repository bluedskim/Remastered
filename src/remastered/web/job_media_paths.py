from __future__ import annotations

import shutil
from pathlib import Path

from remastered.web.job_storage import job_media_storage_owner_id


def job_workspace_dir(work_dir: Path, owner_id: str, job_id: str) -> Path:
    return Path(work_dir) / "users" / owner_id / "jobs" / job_id


def delete_job_workspace(job: dict, work_dir: Path) -> None:
    work_dir = Path(work_dir)
    job_id = (job.get("id") or "").strip()
    if not job_id:
        return
    owner = job_media_storage_owner_id(job, work_dir)
    workspace = job_workspace_dir(work_dir, owner, job_id)
    if workspace.is_dir():
        shutil.rmtree(workspace, ignore_errors=True)
