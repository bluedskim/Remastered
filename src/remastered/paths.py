from __future__ import annotations

from pathlib import Path


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_work_dir() -> Path:
    return Path.home() / ".cache" / "remastered"


def job_dir(work_dir: Path, job_id: str) -> Path:
    return ensure_dir(work_dir / job_id)
