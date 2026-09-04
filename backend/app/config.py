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
    max_clip_duration_seconds: float = 15.6
    supported_clip_extensions: str = "mp4,mov,webm,m4v,avi,mkv"

    # Render geometry and timing. Frames are the unit of truth: xfade snaps
    # offsets to frame boundaries while acrossfade is sample-exact, so planning
    # in floating-point seconds drifts audio from video at every transition.
    # 48000 / 30 == 1600 exactly, so a frame count maps to a whole sample count.
    render_fps: int = 30
    render_width: int = 1920
    render_height: int = 1080
    render_sting_hold_seconds: float = 0.6
    render_sting_blend_seconds: float = 0.3
    render_transition_style: str = "sweep"

    # Brand
    render_brand_blue: str = "#0B5CD5"
    render_brand_orange: str = "#FF7A18"
    render_font_path: Path | None = None
    render_font_size: int = 56
    # Dark outline behind the label, now that there is no backing plate.
    render_label_stroke_width: int = 3
    # Sting logo size as a fraction of the frame's shorter side.
    render_sting_logo_scale: float = 0.34
    max_logo_size_bytes: int = 5242880
    max_logo_dimension: int = 4000

    # Encoding
    render_intermediate_crf: int = 16
    render_intermediate_preset: str = "veryfast"
    render_final_crf: int = 20
    render_final_preset: str = "medium"
    render_audio_bitrate: str = "192k"

    # Safety limits
    render_max_graph_inputs: int = 24
    render_ffmpeg_timeout_seconds: int = 1800
    render_ffprobe_timeout_seconds: int = 60

    @property
    def render_hold_frames(self) -> int:
        return max(0, round(self.render_sting_hold_seconds * self.render_fps))

    @property
    def render_blend_frames(self) -> int:
        return max(1, round(self.render_sting_blend_seconds * self.render_fps))

    @property
    def render_samples_per_frame(self) -> int:
        return 48000 // self.render_fps

    @property
    def supported_extensions(self) -> set[str]:
        return {value.strip().lower().lstrip(".") for value in self.supported_clip_extensions.split(",") if value.strip()}


@lru_cache
def get_settings():
    return Settings()
