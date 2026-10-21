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
    references_dir: Path = Path("/references")
    oauth_redirect_base: str | None = None

    @property
    def references_path(self) -> Path:
        return Path(os.path.expanduser(str(self.references_dir)))
