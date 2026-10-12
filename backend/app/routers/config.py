from __future__ import annotations

from fastapi import APIRouter

from backend.app.config import get_settings
from backend.app.filename_parser import CLIENT_FILENAME_PATTERN
from backend.app.schemas import ClientConfigResponse


router = APIRouter(prefix="/config", tags=["config"])
settings = get_settings()


@router.get("", response_model=ClientConfigResponse)
def client_config():
    """Upload rules for the browser, sourced from the same place the API enforces them.

    The create-movie form previously carried its own copy of the filename regex,
    which had drifted from the server's and accepted names the API rejected.
    """
    return ClientConfigResponse(
        filenamePattern=CLIENT_FILENAME_PATTERN,
        supportedExtensions=sorted(settings.supported_extensions),
        minClipCount=settings.min_clip_count,
        maxClipCount=settings.max_clip_count,
        maxClipSizeBytes=settings.max_clip_size_bytes,
        maxUploadBytes=settings.max_upload_bytes,
        maxClipDurationSeconds=settings.max_clip_duration_seconds,
        supportedSweepFormats=sorted(settings.supported_sweep_formats),
        maxBrandSweepBytes=settings.max_brand_sweep_bytes,
    )
