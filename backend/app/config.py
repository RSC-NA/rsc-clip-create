from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # The API is normally launched from the repository root. Load the backend
    # file first so AUTH_ENABLED and database settings are not silently missed.
    model_config = SettingsConfigDict(env_file=("backend/.env", ".env"), extra="ignore")

    app_name: str = "Clip Create API"
    database_url: str = (
        "mysql+pymysql://clip_create:clip_create@127.0.0.1:3306/clip_create"
    )
    fastapi_public_url: str = "http://127.0.0.1:8000"
    frontend_public_url: str = "http://127.0.0.1:3000"
    discord_client_id: str = "discord-client-id"
    discord_client_secret: str = "discord-client-secret"
    discord_redirect_uri: str = "http://127.0.0.1:8000/api/v1/auth/discord/callback"
    session_cookie_name: str = "clip_create_session"
    oauth_state_cookie_name: str = "clip_create_oauth_state"
    oauth_next_cookie_name: str = "clip_create_oauth_next"
    storage_root: Path = Path("storage")
    veo_provider: str = "veo"
    veo_dry_run: bool = True
    secure_cookies: bool = False
    admin_discord_ids: list[str] = Field(default_factory=list)
    auth_enabled: bool = True
    auth_bypass_admin: bool = True
    auth_bypass_display_name: str = "Local Test User"
    min_clip_count: int = 2
    max_clip_count: int = 100
    max_clip_size_bytes: int = 524288000
    max_clip_duration_seconds: float = 15.6
    supported_clip_extensions: str = "mp4,mov,webm,m4v,avi,mkv"

    @property
    def supported_extensions(self) -> set[str]:
        return {value.strip().lower().lstrip(".") for value in self.supported_clip_extensions.split(",") if value.strip()}


@lru_cache
def get_settings():
    return Settings()
