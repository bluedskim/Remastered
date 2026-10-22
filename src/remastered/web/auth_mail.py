from __future__ import annotations

import logging

import httpx

from remastered.web.auth_settings import AuthSettings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
SUBJECT = "Remastered 회원가입 인증코드"


def verification_body_korean(code: str, ttl_minutes: int) -> str:
    return (
        f"Remastered 회원가입 인증코드: {code}\n\n"
        f"이 코드는 {ttl_minutes}분 후에 만료됩니다. "
        f"요청하지 않았다면 이 메일을 무시하셔도 됩니다."
    )


def send_verification_email(to_email: str, code: str, settings: AuthSettings | None = None) -> None:
    auth = settings or AuthSettings()
    body = verification_body_korean(code, auth.verification_ttl_minutes)
    mode = (auth.mail_mode or "log").lower()
    if mode == "log":
        logger.info(
            "Email verification code for %s: %s (REMASTERED_MAIL_MODE=log; not sent via provider)",
            to_email,
            code,
        )
        return
    if mode != "resend":
        raise RuntimeError(f"Unknown REMASTERED_MAIL_MODE: {auth.mail_mode}")
    if not auth.resend_api_key:
        raise RuntimeError("RESEND_API_KEY is required when REMASTERED_MAIL_MODE=resend")
    payload = {
        "from": auth.mail_from,
        "to": [to_email],
        "subject": SUBJECT,
        "text": body,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {auth.resend_api_key}"},
            json=payload,
        )
        resp.raise_for_status()
