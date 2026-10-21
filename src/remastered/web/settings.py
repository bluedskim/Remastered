from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REMASTERED_WEB_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    password: str | None = None
    session_secret: str = Field(
        default_factory=lambda: os.environ.get(
            "REMASTERED_WEB_SESSION_SECRET", "remastered-dev-session-change-me"
        )
    )
    public_base_url: str = "http://localhost:8080"
    references_dir: Path = Path("/references")
    google_client_id: str | None = None
    google_client_secret: str | None = None
    max_concurrent_jobs_per_user: int = 1
    daily_job_quota_per_user: int = 5
    max_source_duration_seconds: int = 900
    max_source_bytes: int = 500 * 1024 * 1024
    job_create_min_interval_seconds: float = 30.0
    job_retention_days: int = 14
    trusted_proxy_ips: str = "*"
    max_concurrent_media_streams: int = 12
    uvicorn_limit_concurrency: int = 80
    uvicorn_timeout_keep_alive: int = 5

    @property
    def references_path(self) -> Path:
        return Path(os.path.expanduser(str(self.references_dir)))

    def oauth_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/oauth/callback"
