"""Sanitize job errors and logs shown in the web UI and public API."""

from __future__ import annotations

import re
from typing import Any

# Stable reason codes for support (safe to expose).
REASON_TP_SAVE = "true_peak_save_failed"
REASON_TP_VERIFY = "true_peak_verify_failed"
REASON_TP_MEASURE = "true_peak_measure_failed"
REASON_MATCHERING = "mastering_failed"
REASON_SOURCE_DOWNLOAD = "source_download_failed"
REASON_REFERENCE = "reference_failed"
REASON_REFERENCE_FILE = "reference_file_missing"
REASON_PLAYBACK = "playback_prepare_failed"
REASON_INTERNAL = "job_failed"
REASON_VALIDATION = "validation_failed"

MSG_TP_SAVE = (
    "Could not save the protected master file during finalizing. "
    "Try again, or turn off true-peak safety."
)
MSG_TP_VERIFY = (
    "True-peak safety could not verify the output after several attempts. "
    "Try again, or turn off true-peak safety."
)
MSG_TP_MEASURE = "True-peak safety could not measure the mastered audio. Try again."
MSG_MATCHERING = "Mastering failed. Try again or choose a different reference."
MSG_SOURCE_DOWNLOAD = "Source download failed. Check the YouTube URL and try again."
MSG_REFERENCE_DOWNLOAD = "Reference download failed. Try again or pick another reference."
MSG_REFERENCE_FILE = "Reference file is missing. Upload or choose a preset again."
MSG_PLAYBACK = "Playback file preparation failed. Try again."
MSG_INTERNAL = "This job could not be completed. Try again."
MSG_VALIDATION = "This job could not start. Check your inputs and try again."

_SENSITIVE_MARKERS = (
    "traceback",
    "errno",
    "cross-device",
    "invalid cross-device",
    "ffmpeg",
    "yt-dlp",
    "stderr",
    "oauth",
    "token",
    "secret",
    "exception",
    'file "',
    "/tmp/",
    "/work/",
    "\\",
    "shutil",
    "subprocess",
)

_PATH_LIKE = re.compile(
    r"(?:"
    r"/(?:work|tmp|var|home|usr|opt|agent)(?:/[^\s'\"<>]+)+"
    r"|'[^']*(?:/|\\)[^']+'"
    r")",
    re.IGNORECASE,
)
_ERRNO_RE = re.compile(r"\[\s*errno\s*\d+\s*\]", re.I)
_TRACEBACK_RE = re.compile(r"traceback \(most recent call last\)", re.I)

_SAFE_LOG_PREFIXES = (
    "Job added to the queue.",
    "Downloading YouTube audio",
    "Download complete:",
    "Running matchering",
    "Master WAV:",
    "Mastering complete.",
    "Downloading reference audio",
    "Using cached reference audio:",
    "True-peak protection:",
    "Job re-queued after the server restarted",
    "Playback file preparation failed",
)

_STAGE_MESSAGES = {
    "downloading_source": MSG_SOURCE_DOWNLOAD,
    "downloading_reference": MSG_REFERENCE_DOWNLOAD,
    "matching": MSG_MATCHERING,
    "finalizing": MSG_TP_SAVE,
}


def _contains_sensitive(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _SENSITIVE_MARKERS) or bool(_PATH_LIKE.search(text))


def _redact_paths(text: str) -> str:
    return _PATH_LIKE.sub("[path]", text)


def classify_failure_message(raw: str | None, *, processing_stage: str | None = None) -> tuple[str, str]:
    """Map stored/raw failure text to a user-safe message and reason code."""
    if not raw or not str(raw).strip():
        stage = (processing_stage or "").strip()
        return _STAGE_MESSAGES.get(stage, MSG_INTERNAL), REASON_INTERNAL

    text = str(raw).strip()
    low = text.lower()

    if "cross-device" in low or "errno 18" in low or "exdev" in low:
        return MSG_TP_SAVE, REASON_TP_SAVE
    if "true-peak protection failed after" in low:
        return MSG_TP_VERIFY, REASON_TP_VERIFY
    if "could not measure true peak" in low or "true peak measurement failed" in low:
        return MSG_TP_MEASURE, REASON_TP_MEASURE
    if "true-peak protection failed" in low or "true peak protection failed" in low:
        return MSG_TP_VERIFY, REASON_TP_VERIFY
    if "could not download" in low or "reference download" in low:
        return MSG_REFERENCE_DOWNLOAD, REASON_REFERENCE
    if text == "Missing user_id":
        return MSG_INTERNAL, REASON_INTERNAL
    if "filenotfound" in low or "no such file" in low or "not found" in low:
        if (processing_stage or "") == "downloading_reference":
            return MSG_REFERENCE_FILE, REASON_REFERENCE_FILE
        return MSG_INTERNAL, REASON_INTERNAL

    if _contains_sensitive(text) or _TRACEBACK_RE.search(text):
        stage = (processing_stage or "").strip()
        if "master" in low or stage == "matching":
            return MSG_MATCHERING, REASON_MATCHERING
        if stage in _STAGE_MESSAGES:
            reason = {
                "downloading_source": REASON_SOURCE_DOWNLOAD,
                "downloading_reference": REASON_REFERENCE,
                "matching": REASON_MATCHERING,
                "finalizing": REASON_TP_SAVE,
            }[stage]
            return _STAGE_MESSAGES[stage], reason
        return MSG_INTERNAL, REASON_INTERNAL

    redacted = _redact_paths(text)
    if _contains_sensitive(redacted):
        return MSG_INTERNAL, REASON_INTERNAL
    return redacted, REASON_VALIDATION


def user_facing_job_error(
    raw: str | None,
    *,
    processing_stage: str | None = None,
) -> str | None:
    if raw is None:
        return None
    if not str(raw).strip():
        return None
    msg, _ = classify_failure_message(raw, processing_stage=processing_stage)
    return msg


def user_facing_job_record(job: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with sanitized ``error`` and optional ``failure_reason``."""
    data = dict(job)
    raw_err = data.get("error")
    if raw_err:
        msg, reason = classify_failure_message(
            str(raw_err),
            processing_stage=data.get("processing_stage"),
        )
        data["error"] = msg
        data["failure_reason"] = reason
    else:
        data.pop("failure_reason", None)
    return data


def sanitize_log_message(message: str) -> str:
    """Redact or replace a single job log line for user display."""
    if not message:
        return message
    text = message.strip()
    if any(text.startswith(p) for p in _SAFE_LOG_PREFIXES):
        return _redact_paths(text)

    if text.startswith("Error:"):
        tail = text[6:].strip()
        safe, _ = classify_failure_message(tail)
        return safe

    if _TRACEBACK_RE.search(text) or "  File " in text:
        return "An internal error occurred. Try again."

    if "Playback transcode failed" in text:
        if "(original)" in text:
            return "Playback file preparation failed (original)."
        return "Playback file preparation failed (mastered)."

    if "True-peak protection failed" in text:
        safe, _ = classify_failure_message(text)
        return safe

    if _contains_sensitive(text):
        safe, _ = classify_failure_message(text)
        return safe

    return _redact_paths(text)


def prepare_job_logs(logs: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {**line, "message": sanitize_log_message(line.get("message", ""))}
        for line in logs
    ]


def user_facing_optional_error(raw: str | None) -> str | None:
    """Sanitize arbitrary error strings (e.g. video build API)."""
    if not raw or not str(raw).strip():
        return None
    msg, _ = classify_failure_message(str(raw))
    return msg
