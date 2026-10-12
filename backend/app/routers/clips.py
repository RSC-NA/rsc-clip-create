from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.routing import APIRoute
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.deps import get_current_user
from backend.app.config import get_settings
from backend.app.filename_parser import parse_clip_filename
from backend.app.models import Clip, Movie, MovieSlot, RenderJobClipResult, User
from backend.app.schemas import ClipResponse
from backend.app.storage import (
    MAX_FILENAME_BYTES,
    ProbeError,
    UploadTooLarge,
    probe_media,
    safe_upload_name,
    save_upload,
)


logger = logging.getLogger(__name__)
settings = get_settings()


def _request_too_large(size: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=(
            f"That upload is {size / 1e6:.0f} MB; the limit is "
            f"{settings.max_upload_bytes / 1e6:.0f} MB per request. "
            "Upload fewer clips at a time."
        ),
    )


class ClipUploadRoute(APIRoute):
    """Count the multipart bytes before FastAPI parses File parameters.

    The upload handler only sees already-parsed files, whose sizes exclude
    multipart boundaries and headers. Wrapping the ASGI receive stream keeps
    the check request-wide even when Content-Length is absent or incorrect.
    """

    def get_route_handler(self):
        handler = super().get_route_handler()
        if self.methods != {"POST"}:
            return handler

        async def limit_upload(request: Request):
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes:
                raise _request_too_large(int(declared))

            received = 0
            receive = request.receive

            async def limited_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > settings.max_upload_bytes:
                        raise _request_too_large(received)
                return message

            return await handler(Request(request.scope, receive=limited_receive))

        return limit_upload


router = APIRouter(prefix="/clips", tags=["clips"], route_class=ClipUploadRoute)


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
        raise HTTPException(status_code=400, detail="Select at least one clip to upload.")
    if len(files) > settings.max_clip_count:
        raise HTTPException(status_code=400, detail=f"Upload no more than {settings.max_clip_count} clips.")

    parsed_files = []
    storage_names = []
    for file in files:
        try:
            parsed_files.append(parse_clip_filename(file.filename or "", settings.supported_extensions))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{file.filename}: {exc}") from exc
        # Validate the actual name that will be written, not the unprefixed
        # upload name. Do this for every file before opening any destination.
        storage_name = safe_upload_name(str(uuid.uuid4()), file.filename, "clip.mp4")
        if len(storage_name.encode("utf-8")) > MAX_FILENAME_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"{file.filename}: filename is too long for clip storage.",
            )
        storage_names.append(storage_name)

    saved_clips: list[Clip] = []
    created_paths: list[Path] = []

    def _abort(detail: str) -> HTTPException:
        return HTTPException(status_code=400, detail=detail)

    try:
        for file, parsed, storage_name in zip(files, parsed_files, storage_names):
            try:
                # The size limit is enforced while streaming, not after the whole
                # file has already been written to disk.
                destination = save_upload(
                    file,
                    storage_name,
                    max_bytes=settings.max_clip_size_bytes,
                )
            except UploadTooLarge:
                raise _abort(f"Clip {file.filename} exceeds the file-size limit.") from None
            created_paths.append(destination)

            try:
                media = probe_media(destination, strict=True)
            except ProbeError as exc:
                raise _abort(f"Clip {file.filename} could not be read as video.") from exc
            if not media.get("has_video"):
                raise _abort(f"Clip {file.filename} has no video stream.")

            duration = media.get("duration")
            if duration is None or duration <= 0:
                # Accepting this defers the failure to render time, where it kills
                # the whole movie instead of one upload.
                raise _abort(f"Clip {file.filename} has no readable duration.")
            if duration > settings.max_clip_duration_seconds:
                raise _abort(
                    f"Clip {file.filename} exceeds the "
                    f"{settings.max_clip_duration_seconds:g} second limit."
                )

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
    except Exception:
        # Neither a failed flush nor a failed commit may leave files behind.
        # Roll back validation failures too, since earlier clips in a batch
        # may already have been flushed into this transaction.
        try:
            db.rollback()
        finally:
            for path in created_paths:
                path.unlink(missing_ok=True)
        raise
    return [_clip_to_response(clip) for clip in saved_clips]


@router.delete("/{clip_id}", status_code=204)
def delete_clip(
    clip_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a clip and its file.

    A clip is referenced from two places, not one: MovieSlot (the timeline) and
    RenderJobClipResult (past render diagnostics). Refusing only on the former
    would still hit the foreign key from the latter, so the historical results
    are detached instead -- their clipName already falls back to "Deleted clip".
    """
    clip = db.scalar(select(Clip).where(Clip.id == clip_id, Clip.user_id == user.id))
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")

    titles = db.scalars(
        select(Movie.title)
        .join(MovieSlot, MovieSlot.movie_id == Movie.id)
        .where(MovieSlot.clip_id == clip.id)
        .distinct()
    ).all()
    if titles:
        listed = ", ".join(sorted(titles)[:5])
        raise HTTPException(
            status_code=409,
            detail=f"This clip is still used by: {listed}. Remove it from those timelines first.",
        )

    db.execute(
        update(RenderJobClipResult)
        .where(RenderJobClipResult.clip_id == clip.id)
        .values(clip_id=None)
    )
    path = clip.storage_path
    db.delete(clip)
    db.commit()

    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove %s", path, exc_info=True)
    logger.info("deleted clip clip_id=%s", clip_id)
    return None
