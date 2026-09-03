from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.deps import get_current_user
from backend.app.config import get_settings
from backend.app.filename_parser import parse_clip_filename
from backend.app.models import Clip, Movie, MovieSlot, User
from backend.app.schemas import ClipResponse
from backend.app.storage import probe_duration, save_upload


router = APIRouter(prefix="/clips", tags=["clips"])
settings = get_settings()


def _clip_to_response(clip: Clip) -> ClipResponse:
    return ClipResponse(
        id=clip.id,
        name=clip.original_name,
        durationSeconds=clip.duration_seconds,
        status=clip.status,
        storagePath=clip.storage_path,
        team=clip.team,
        player=clip.player,
        tier=clip.tier,
        extension=clip.extension,
    )


@router.get("", response_model=list[ClipResponse])
def list_clips(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    clips = db.scalars(
        select(Clip).where(Clip.user_id == user.id).order_by(Clip.created_at.desc())
    ).all()
    return [_clip_to_response(clip) for clip in clips]


@router.post("", response_model=list[ClipResponse])
def upload_clips(
    files: list[UploadFile] = File(...),
    movie_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least two clips.")
    if len(files) > settings.max_clip_count:
        raise HTTPException(status_code=400, detail=f"Upload no more than {settings.max_clip_count} clips.")

    parsed_files = []
    for file in files:
        try:
            parsed_files.append(parse_clip_filename(file.filename or "", settings.supported_extensions))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{file.filename}: {exc}") from exc

    saved_clips: list[Clip] = []
    created_paths = []
    for file, parsed in zip(files, parsed_files):
        destination = save_upload(file, f"{uuid.uuid4()}-{file.filename}")
        created_paths.append(destination)
        if destination.stat().st_size > settings.max_clip_size_bytes:
            for path in created_paths:
                path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Clip {file.filename} exceeds the file-size limit.")
        duration = probe_duration(destination)
        if duration is not None and duration > settings.max_clip_duration_seconds:
            for path in created_paths:
                path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Clip {file.filename} exceeds the {settings.max_clip_duration_seconds:g} second limit.")

        clip = Clip(
            user_id=user.id,
            original_name=file.filename or "clip.mp4",
            storage_path=str(destination),
            duration_seconds=duration,
            metadata_json={"content_type": file.content_type},
            team=parsed.team,
            player=parsed.player,
            tier=parsed.tier,
            extension=parsed.extension,
        )
        db.add(clip)
        db.flush()
        saved_clips.append(clip)

    if movie_id:
        movie = db.scalar(select(Movie).where(Movie.id == movie_id, Movie.user_id == user.id))
        if movie:
            next_index = max((slot.sort_index for slot in movie.slots), default=-1) + 1
            for offset, clip in enumerate(saved_clips):
                db.add(MovieSlot(movie_id=movie.id, clip_id=clip.id, sort_index=next_index + offset, overlay_text=""))

    db.commit()
    return [_clip_to_response(clip) for clip in saved_clips]
