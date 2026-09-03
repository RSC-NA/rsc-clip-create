from __future__ import annotations

import uuid
from pathlib import Path

from backend.app.config import get_settings


settings = get_settings()


class VeoClient:
    def submit_render(self, clip_paths: list[Path], overlay_texts: list[str]) -> dict:
        if settings.veo_dry_run:
            return {
                "request_id": str(uuid.uuid4()),
                "status": "submitted",
                "clip_count": len(clip_paths),
                "overlay_count": len([text for text in overlay_texts if text]),
            }

        raise NotImplementedError("Wire this client to the real Veo API.")
