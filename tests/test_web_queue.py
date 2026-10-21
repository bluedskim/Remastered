from __future__ import annotations

from pathlib import Path

from remastered.web.db import JobStore


def _create_pending(store: JobStore, user_id: str, url: str) -> str:
    return store.create_job(
        user_id=user_id,
        youtube_url=url,
        reference_path="/references/ref.wav",
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )


def test_queue_position(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    j1 = _create_pending(store, "u1", "https://youtu.be/1")
    j2 = _create_pending(store, "u1", "https://youtu.be/2")
    assert store.queue_position(j1) == 1
    assert store.queue_position(j2) == 2


def test_fair_scheduler_round_robin(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    a1 = _create_pending(store, "alice", "https://youtu.be/a1")
    b1 = _create_pending(store, "bob", "https://youtu.be/b1")
    assert store.next_pending_fair(None) == a1
    store.update_job(a1, status="running")
    a2 = _create_pending(store, "alice", "https://youtu.be/a2")
    assert store.next_pending_fair("alice") == b1
    assert store.next_pending_fair("bob") == a2
