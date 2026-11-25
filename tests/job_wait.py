"""Poll job HTMX panel until terminal state (HTTP 286)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Under parallel CI / loaded workers, finalize can exceed 2s; cap avoids flaky false negatives.
DEFAULT_JOB_COMPLETE_TIMEOUT_S = 30.0
DEFAULT_JOB_COMPLETE_POLL_S = 0.05


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float = DEFAULT_JOB_COMPLETE_TIMEOUT_S,
    poll_s: float = DEFAULT_JOB_COMPLETE_POLL_S,
    failure_message: str = "condition not met",
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(poll_s)
    pytest.fail(f"{failure_message} (timeout {timeout_s}s)")


def wait_job_complete(
    client: TestClient,
    job_id: str,
    *,
    timeout_s: float = DEFAULT_JOB_COMPLETE_TIMEOUT_S,
    poll_s: float = DEFAULT_JOB_COMPLETE_POLL_S,
) -> None:
    last_status: int | None = None

    def done() -> bool:
        nonlocal last_status
        panel = client.get(f"/jobs/{job_id}/panel")
        last_status = panel.status_code
        return panel.status_code == 286

    wait_until(
        done,
        timeout_s=timeout_s,
        poll_s=poll_s,
        failure_message=(
            f"job {job_id} did not complete"
            + (f" (last panel HTTP status {last_status})" if last_status is not None else "")
        ),
    )


def wait_job_store_completed(
    store: Any,
    job_id: str,
    *,
    timeout_s: float = DEFAULT_JOB_COMPLETE_TIMEOUT_S,
    poll_s: float = DEFAULT_JOB_COMPLETE_POLL_S,
) -> dict:
    job: dict | None = None

    def done() -> bool:
        nonlocal job
        job = store.get_job(job_id)
        return bool(job and job.get("status") == "completed")

    wait_until(
        done,
        timeout_s=timeout_s,
        poll_s=poll_s,
        failure_message=f"job {job_id} did not reach completed status",
    )
    assert job is not None
    return job