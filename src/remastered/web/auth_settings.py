from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    jwt_secret: str = Field(
        default_factory=lambda: os.environ.get(
            "REMASTERED_JWT_SECRET", "remastered-dev-jwt-change-me"
        )
    )
    jwt_expiry_seconds: int = Field(
        default_factory=lambda: int(
            os.environ.get("REMASTERED_JWT_EXPIRY_SECONDS", "86400")
        )
    )
    mail_mode: str = Field(
        default_factory=lambda: os.environ.get("REMASTERED_MAIL_MODE", "log").lower()
    )
    resend_api_key: str | None = Field(
        default_factory=lambda: os.environ.get("RESEND_API_KEY")
    )
    mail_from: str = Field(
        default_factory=lambda: os.environ.get("MAIL_FROM", "Remastered <onboarding@resend.dev>")
    )
    verification_ttl_minutes: int = 10
    verification_resend_cooldown_seconds: int = 60
    verification_max_attempts: int = 5
    verification_max_sends_per_email_per_hour: int = 5
    verification_max_sends_per_ip_per_hour: int = 20


def auth_cookie_secure(public_base_url: str) -> bool:
    return public_base_url.lower().startswith("https://")
