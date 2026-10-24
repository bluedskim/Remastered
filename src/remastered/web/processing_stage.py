from __future__ import annotations

from typing import Any

from remastered.web.reference_youtube import is_youtube_reference_job

STAGE_QUEUED = "queued"
STAGE_DOWNLOADING_SOURCE = "downloading_source"
STAGE_DOWNLOADING_REFERENCE = "downloading_reference"
STAGE_MATCHING = "matching"
STAGE_FINALIZING = "finalizing"
STAGE_DONE = "done"

PIPELINE_STAGE_DEFS: tuple[tuple[str, str], ...] = (
    (STAGE_QUEUED, "Queued"),
    (STAGE_DOWNLOADING_SOURCE, "Downloading source"),
    (STAGE_DOWNLOADING_REFERENCE, "Downloading reference"),
    (STAGE_MATCHING, "Matching"),
    (STAGE_FINALIZING, "Finalizing"),
    (STAGE_DONE, "Done"),
)


def pipeline_stage_defs_for_job(job: dict[str, Any]) -> list[tuple[str, str]]:
    stages = list(PIPELINE_STAGE_DEFS)
    if not is_youtube_reference_job(job):
        stages = [s for s in stages if s[0] != STAGE_DOWNLOADING_REFERENCE]
    return stages


def pipeline_steps_for_job(job: dict[str, Any]) -> list[dict[str, str]]:
    """Stepper states for UI: pending, running, done, failed, skipped."""
    defs = pipeline_stage_defs_for_job(job)
    status = (job.get("status") or "").strip()
    current = (job.get("processing_stage") or STAGE_QUEUED).strip()
    keys = [k for k, _ in defs]
    if current not in keys:
        current = STAGE_QUEUED

    steps: list[dict[str, str]] = []
    for key, label in defs:
        if status == "completed":
            state = "done"
        elif status == "failed":
            if key == current:
                state = "failed"
            elif keys.index(key) < keys.index(current):
                state = "done"
            else:
                state = "pending"
        elif status == "pending":
            state = "running" if key == STAGE_QUEUED else "pending"
        elif status == "running":
            idx = keys.index(key)
            cur_idx = keys.index(current)
            if idx < cur_idx:
                state = "done"
            elif idx == cur_idx:
                state = "running"
            else:
                state = "pending"
        else:
            state = "pending"
        steps.append({"key": key, "label": label, "state": state})
    return steps
