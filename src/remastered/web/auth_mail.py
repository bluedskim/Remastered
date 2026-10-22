from __future__ import annotations

import logging

import httpx

from remastered.web.auth_settings import AuthSettings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
SUBJECT = "Remastered sign-up verification code"


def verification_body_english(code: str, ttl_minutes: int) -> str:
    return (
        f"Your Remastered verification code is: {code}\n\n"
        f"This code expires in {ttl_minutes} minutes. "
        f"If you did not request this, you can ignore this email."
    )


def send_verification_email(to_email: str, code: str, settings: AuthSettings | None = None) -> None:
    auth = settings or AuthSettings()
    body = verification_body_english(code, auth.verification_ttl_minutes)
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
