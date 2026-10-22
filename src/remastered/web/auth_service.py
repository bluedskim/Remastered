from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from remastered.web.auth_mail import send_verification_email
from remastered.web.auth_password import hash_secret, verify_secret
from remastered.web.auth_settings import AuthSettings
from remastered.web.auth_validation import (
    VERIFICATION_CODE_REGEX,
    require_valid_email,
    require_valid_nickname,
    require_valid_password,
)
from remastered.web.db import JobStore


def _parse_utc(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthService:
    def __init__(self, store: JobStore, settings: AuthSettings | None = None) -> None:
        self.store = store
        self.settings = settings or AuthSettings()

    def is_nickname_available(self, raw_nickname: str) -> bool:
        nickname = require_valid_nickname(raw_nickname)
        return not self.store.is_nickname_taken(nickname)

    def send_verification_code(self, raw_email: str, client_ip: str) -> None:
        email = require_valid_email(raw_email)
        if self.store.get_auth_user_by_login_id(email):
            raise AuthError("This email is already registered.", status_code=409)

        since = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        if (
            self.store.count_verification_sends_since(email=email, since_iso=since)
            >= self.settings.verification_max_sends_per_email_per_hour
        ):
            raise AuthError(
                "Too many verification code requests. Please try again later.",
                status_code=429,
            )
        if (
            self.store.count_verification_sends_since(ip=client_ip, since_iso=since)
            >= self.settings.verification_max_sends_per_ip_per_hour
        ):
            raise AuthError(
                "Too many verification code requests. Please try again later.",
                status_code=429,
            )

        existing = self.store.get_email_verification(email)
        if existing:
            sent_at = _parse_utc(existing["sent_at"])
            cooldown_end = sent_at + timedelta(
                seconds=self.settings.verification_resend_cooldown_seconds
            )
            if datetime.now(timezone.utc) < cooldown_end:
                raise AuthError(
                    "Please wait before requesting another verification code.",
                    status_code=429,
                )

        code = f"{secrets.randbelow(1_000_000):06d}"
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=self.settings.verification_ttl_minutes)
        self.store.upsert_email_verification(
            email=email,
            code_hash=hash_secret(code),
            expires_at=expires.isoformat(),
            sent_at=now.isoformat(),
        )
        self.store.record_verification_send(ip=client_ip, email=email)
        send_verification_email(email, code, self.settings)

    def signup(
        self,
        *,
        login_id: str,
        password: str,
        nickname: str,
        verification_code: str,
        anon_id: str,
    ) -> dict[str, str]:
        email = require_valid_email(login_id)
        nick = require_valid_nickname(nickname)
        require_valid_password(password)
        code = verification_code.strip()
        if not VERIFICATION_CODE_REGEX.match(code):
            raise AuthError("Verification code must be 6 digits.")

        if self.store.get_auth_user_by_login_id(email):
            raise AuthError("This email is already registered.", status_code=409)
        if self.store.is_nickname_taken(nick):
            raise AuthError("This nickname is already taken.", status_code=409)

        self._verify_and_consume_code(email, code)

        user_id = self.store.create_auth_user(
            login_id=email,
            nickname=nick,
            password_hash=hash_secret(password),
        )
        self.store.claim_jobs(anon_id, user_id)
        user = self.store.get_auth_user_by_id(user_id)
        assert user is not None
        return user

    def login(self, login_id: str, password: str, anon_id: str) -> dict[str, str]:
        email = require_valid_email(login_id)
        user = self.store.get_auth_user_by_login_id(email)
        if user is None or not verify_secret(password, user["password_hash"]):
            raise AuthError("Invalid email or password.", status_code=401)
        self.store.claim_jobs(anon_id, user["id"])
        return user

    def _verify_and_consume_code(self, email: str, code: str) -> None:
        verification = self.store.get_email_verification(email)
        if verification is None:
            raise AuthError("Verification code not found or expired.")

        expires_at = _parse_utc(verification["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            self.store.delete_email_verification(email)
            raise AuthError("Verification code has expired.")

        if verification["failed_attempts"] >= self.settings.verification_max_attempts:
            self.store.delete_email_verification(email)
            raise AuthError(
                "Too many failed attempts. Please request a new verification code."
            )

        if not verify_secret(code, verification["code_hash"]):
            attempts = self.store.increment_verification_failed_attempts(email)
            if attempts >= self.settings.verification_max_attempts:
                self.store.delete_email_verification(email)
                raise AuthError(
                "Too many failed attempts. Please request a new verification code."
            )
            raise AuthError("Incorrect verification code.")

        self.store.delete_email_verification(email)
