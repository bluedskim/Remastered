from __future__ import annotations


def identity_can_access_job(
    job: dict,
    *,
    account_id: str | None,
    anon_id: str,
) -> bool:
    """True when this browser identity may read/delete/update the job record."""
    uid = (job.get("user_id") or "").strip()
    anon = (anon_id or "").strip()
    account = (account_id or "").strip() or None

    if uid and anon and uid == anon:
        return True
    if account and uid == account:
        return True
    return False
