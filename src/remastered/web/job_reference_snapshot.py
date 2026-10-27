from __future__ import annotations

import shutil
from pathlib import Path

from remastered.web.job_storage import job_media_storage_owner_id


def job_reference_snapshot_path(
    work_dir: Path,
    owner_id: str,
    job_id: str,
    filename: str,
) -> Path:
    safe_name = Path(filename).name
    return (
        Path(work_dir)
        / "users"
        / owner_id
        / "jobs"
        / job_id
        / "reference"
        / safe_name
    )


def job_reference_snapshot_dir(work_dir: Path, owner_id: str, job_id: str) -> Path:
    return Path(work_dir) / "users" / owner_id / "jobs" / job_id / "reference"


def is_job_upload_reference_snapshot(
    path: Path | str,
    job: dict,
    work_dir: Path,
) -> bool:
    """True if path is this job's owned upload-reference snapshot."""
    if (job.get("reference_source_type") or "").strip() != "upload":
        return False
    p = Path(path)
    job_id = (job.get("id") or "").strip()
    owner = (job.get("user_id") or "").strip()
    if not job_id or not owner:
        return False
    expected_root = job_reference_snapshot_dir(work_dir, owner, job_id).resolve()
    try:
        p.resolve().relative_to(expected_root)
        return True
    except ValueError:
        return False


def snapshot_upload_reference_for_job(
    work_dir: Path,
    owner_id: str,
    job_id: str,
    source: Path,
) -> Path:
    """Copy upload reference audio into job-owned storage."""
    work_dir = Path(work_dir)
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Reference file not found: {source}")
    dest = job_reference_snapshot_path(work_dir, owner_id, job_id, source.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


def delete_job_reference_snapshot(job: dict, work_dir: Path) -> None:
    work_dir = Path(work_dir)
    owner = job_media_storage_owner_id(job, work_dir)
    job_id = (job.get("id") or "").strip()
    if not job_id:
        return
    snap_dir = job_reference_snapshot_dir(work_dir, owner, job_id)
    if snap_dir.is_dir():
        shutil.rmtree(snap_dir.parent, ignore_errors=True)
