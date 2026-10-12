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
    secure_cookies: bool = False
    admin_discord_ids: list[str] = Field(default_factory=list)
    auth_enabled: bool = True
    auth_bypass_admin: bool = True
    auth_bypass_display_name: str = "Local Test User"
    min_clip_count: int = 2
    max_clip_count: int = 100
    max_clip_size_bytes: int = 524288000
    # Total bytes for one upload request, across every file in it. Uploads
    # reach the API through the Next rewrite, and Next buffers the entire
    # request body in memory -- so this must match
    # experimental.proxyClientMaxBodySize in next.config.ts, which reads the
    # same MAX_UPLOAD_BYTES value at build time.
    max_upload_bytes: int = 536870912
    max_clip_duration_seconds: float = 31
    supported_clip_extensions: str = "mp4,mov,webm,m4v,avi,mkv"

    # Render geometry. Frames are the unit of truth: trim and tpad take frame
    # counts, and 48000 / 30 == 1600 exactly, so a frame count maps to a whole
    # sample count and video never drifts from audio.
    render_fps: int = 30
    render_width: int = 1920
    render_height: int = 1080

    # Brand
    # The sweep overlaid across every cut. Unset means the bundled stinger at
    # backend/app/assets/stings/; an account can also upload its own, which
    # takes precedence over both. See services/brand.py:BUNDLED_SWEEP for what
    # a replacement file has to look like.
    render_brand_sweep_path: Path | None = None
    max_brand_sweep_bytes: int = 209715200
    supported_sweep_extensions: str = "mov,webm,mp4,mkv"
    render_font_path: Path | None = None
    render_font_size: int = 56
    # Dark outline behind the label, now that there is no backing plate.
    render_label_stroke_width: int = 3

    # Encoding
    render_intermediate_crf: int = 16
    render_intermediate_preset: str = "veryfast"
    render_final_crf: int = 20
    render_final_preset: str = "medium"
    render_audio_bitrate: str = "192k"

    # Safety limits
    # Clips joined per ffmpeg pass. This mattered more when transitions were
    # chained xfades, which buffered about 0.8GB per clip at 1080p; overlaying
    # the sweep is cheap, but grouping still bounds the open decoder count.
    render_chunk_size: int = 4
    render_max_graph_inputs: int = 24
    render_ffmpeg_timeout_seconds: int = 1800
    render_ffprobe_timeout_seconds: int = 60

    @property
    def render_samples_per_frame(self) -> int:
        return 48000 // self.render_fps

    @property
    def supported_extensions(self) -> set[str]:
        return _extensions(self.supported_clip_extensions)

    @property
    def supported_sweep_formats(self) -> set[str]:
        return _extensions(self.supported_sweep_extensions)


def _extensions(value: str) -> set[str]:
    return {item.strip().lower().lstrip(".") for item in value.split(",") if item.strip()}


@lru_cache
def get_settings():
    return Settings()
