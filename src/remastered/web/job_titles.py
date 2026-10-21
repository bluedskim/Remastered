from __future__ import annotations

import re
from typing import Any

DOWNLOAD_TITLE_LOG_RE = re.compile(r"^다운로드 완료:\s*(.+?)\s*$")


def parse_title_from_log_message(message: str) -> str | None:
    match = DOWNLOAD_TITLE_LOG_RE.match(message.strip())
    if not match:
        return None
    title = match.group(1).strip()
    return title or None


def parse_title_from_logs(logs: list[dict[str, str]]) -> str | None:
    for line in logs:
        title = parse_title_from_log_message(line.get("message", ""))
        if title:
            return title
    return None


def job_display_title(job: dict[str, Any]) -> str:
    title = (job.get("title") or "").strip()
    if title:
        return title
    if job.get("status") in ("pending", "running"):
        return "제목 확인 중…"
    return (job.get("youtube_url") or "").strip()
