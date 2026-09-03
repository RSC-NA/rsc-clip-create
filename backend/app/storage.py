from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from fastapi import UploadFile

from backend.app.config import get_settings


settings = get_settings()


def ensure_storage_dirs() -> None:
    for folder in ("clips", "renders"):
        (settings.storage_root / folder).mkdir(parents=True, exist_ok=True)


def save_upload(file: UploadFile, destination_name: str) -> Path:
    ensure_storage_dirs()
    destination = settings.storage_root / "clips" / Path(destination_name).name
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    return destination


def output_path(movie_id: str, attempt_id: str) -> Path:
    ensure_storage_dirs()
    return settings.storage_root / "renders" / f"{movie_id}-{attempt_id}.mp4"


def probe_duration(path: Path) -> float | None:
    media = probe_media(path)
    return media.get("duration")


def probe_media(path: Path) -> dict:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        duration = payload.get("format", {}).get("duration")
        streams = payload.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        return {
            "duration": float(duration) if duration is not None else None,
            "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
            "width": video.get("width"),
            "height": video.get("height"),
            "frame_rate": video.get("r_frame_rate"),
        }
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError, json.JSONDecodeError):
        return {}
