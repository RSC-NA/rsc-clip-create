from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from fastapi import UploadFile

from backend.app.config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()

STORAGE_FOLDERS = ("clips", "renders", "logos")
_COPY_CHUNK = 1024 * 1024


class ProbeError(RuntimeError):
    """ffprobe could not describe a media file."""


class UploadTooLarge(RuntimeError):
    """An upload exceeded its byte budget and was discarded mid-write."""


def ensure_storage_dirs() -> None:
    for folder in STORAGE_FOLDERS:
        (settings.storage_root / folder).mkdir(parents=True, exist_ok=True)


def save_upload(
    file: UploadFile,
    destination_name: str,
    *,
    folder: str = "clips",
    max_bytes: int | None = None,
) -> Path:
    """Stream an upload to disk, aborting as soon as it exceeds ``max_bytes``.

    The size limit is enforced *during* the write. Buffering the whole file
    first and checking afterwards lets a caller fill the disk before the check
    ever runs.
    """
    ensure_storage_dirs()
    # Basename the caller's composed name, then guard the result. Taking .name
    # of "<uuid>-sub/Falcons-Ace-Gold.mp4" yields "Falcons-Ace-Gold.mp4" --
    # the uuid prefix is discarded and every user writes to the same path.
    # Callers must basename the untrusted part before composing (see
    # safe_upload_name); this is the backstop.
    safe_name = Path(destination_name).name
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError(f"Unsafe upload name: {destination_name!r}")
    destination = settings.storage_root / folder / safe_name
    written = 0
    try:
        with destination.open("wb") as output:
            while chunk := file.file.read(_COPY_CHUNK):
                written += len(chunk)
                if max_bytes is not None and written > max_bytes:
                    raise UploadTooLarge(
                        f"{file.filename or destination.name} exceeds the "
                        f"{max_bytes} byte limit."
                    )
                output.write(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def safe_upload_name(prefix: str, filename: str | None, fallback: str = "upload") -> str:
    """Compose a storage name whose uniqueness prefix cannot be stripped.

    ``Path(...).name`` is applied to the untrusted half *before* joining, so a
    filename containing a separator cannot collapse the prefix away.
    """
    base = Path(filename or "").name.strip() or fallback
    return f"{prefix}-{base}"


def output_path(movie_id: str, attempt_id: str) -> Path:
    ensure_storage_dirs()
    return settings.storage_root / "renders" / f"{movie_id}-{attempt_id}.mp4"


def probe_duration(path: Path, *, strict: bool = False) -> float | None:
    return probe_media(path, strict=strict).get("duration")


def probe_media(path: Path, *, strict: bool = False) -> dict:
    """Describe a media file with ffprobe.

    ``strict`` turns a probe failure into a ``ProbeError`` instead of an empty
    dict. Callers that branch on ``has_audio`` MUST use strict mode: an empty
    dict is indistinguishable from "this file has no audio track", and treating
    a probe failure as "no audio" silently replaces real audio with silence.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height,"
                "r_frame_rate,nb_frames,sample_rate",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=settings.render_ffprobe_timeout_seconds,
        )
        payload = json.loads(result.stdout)
        duration = payload.get("format", {}).get("duration")
        streams = payload.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        return {
            "duration": float(duration) if duration is not None else None,
            "has_video": bool(video),
            "has_audio": bool(audio),
            "width": video.get("width"),
            "height": video.get("height"),
            "frame_rate": video.get("r_frame_rate"),
            "nb_frames": int(video["nb_frames"]) if str(video.get("nb_frames", "")).isdigit() else None,
            "video_codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"),
        }
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        # OSError covers ENOMEM/EAGAIN on fork under load and EACCES; without
        # it a probe failure escaped as a 500 on upload.
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        if strict:
            raise ProbeError(f"Could not probe {path.name}: {str(detail).strip()[:500]}") from exc
        logger.warning("ffprobe failed for %s: %s", path, exc)
        return {}
