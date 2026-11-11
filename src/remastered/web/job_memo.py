from __future__ import annotations

JOB_MEMO_MAX_LENGTH = 100

MEMO_TOO_LONG_MESSAGE = (
    f"Memo must be at most {JOB_MEMO_MAX_LENGTH} characters."
)
MEMO_INVALID_BODY_MESSAGE = "Invalid memo request."
MEMO_SAVE_FAILED_MESSAGE = "Could not save your memo. Try again."


def memo_codepoint_length(text: str) -> int:
    """Count Unicode code points (correct for Korean and most user text)."""
    return len(text)


def normalize_job_memo(raw: object) -> tuple[str | None, str | None]:
    """Return (memo, error_detail) — error_detail is set when validation fails."""
    if raw is None:
        return "", None
    if not isinstance(raw, str):
        return None, MEMO_INVALID_BODY_MESSAGE
    memo = raw.strip()
    if memo_codepoint_length(memo) > JOB_MEMO_MAX_LENGTH:
        return None, MEMO_TOO_LONG_MESSAGE
    return memo, None
