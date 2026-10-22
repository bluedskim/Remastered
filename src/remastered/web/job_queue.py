from __future__ import annotations

from typing import Any

from remastered.web.db import JobStore

STATUS_LABELS: dict[str, str] = {
    "pending": "Queued",
    "running": "Processing",
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


def job_status_display(job: dict[str, Any], store: JobStore) -> str:
    status = job.get("status") or ""
    label = STATUS_LABELS.get(status, status)
    if status == "pending":
        position = store.queue_position(job["id"])
        if position > 0:
            return f"{label} (#{position})"
    return label
