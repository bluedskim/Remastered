from __future__ import annotations

from typing import Any

from remastered.web.db import JobStore

STATUS_LABELS_KO: dict[str, str] = {
    "pending": "대기 중",
    "running": "처리 중",
    "completed": "완료",
    "failed": "실패",
    "cancelled": "취소됨",
}


def job_status_display(job: dict[str, Any], store: JobStore) -> str:
    status = job.get("status") or ""
    label = STATUS_LABELS_KO.get(status, status)
    if status == "pending":
        position = store.queue_position(job["id"])
        if position > 0:
            return f"{label} ({position}번째)"
    return label
