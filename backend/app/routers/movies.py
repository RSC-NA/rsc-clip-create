from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from fastapi import APIRouter, Depends, HTTPException

from backend.app.database import get_db
from backend.app.config import get_settings
from backend.app.deps import get_current_user
from backend.app.models import (
    Clip,
    Movie,
    MovieSlot,
    RenderJob,
    RenderJobAttempt,
    RenderJobClipResult,
    User,
    Video,
)
from backend.app.schemas import (
    ClipResponse,
    MovieCreateRequest,
    MovieDetailResponse,
    MovieSlotResponse,
    MovieSummaryResponse,
    MovieTimelineUpdateRequest,
    RenderAttemptResponse,
    RenderClipResultResponse,
    RenderJobResponse,
    VideoSummaryResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/movies", tags=["movies"])
settings = get_settings()


def _video_summary(video: Video | None) -> VideoSummaryResponse | None:
    if video is None:
        return None
    return VideoSummaryResponse(
        id=video.id,
        movieId=video.movie_id,
        storagePath=video.storage_path,
        status=video.status,
        createdAt=video.created_at,
        durationSeconds=video.duration_seconds,
        downloadUrl=f"{settings.fastapi_public_url}/api/v1/videos/{video.id}/file",
    )


def _clip_response(clip: Clip) -> ClipResponse:
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


def _slot_response(slot: MovieSlot) -> MovieSlotResponse:
    return MovieSlotResponse(
        id=slot.id,
        sortIndex=slot.sort_index,
        overlayText=slot.overlay_text,
        clipId=slot.clip_id,
    )


def _job_response(job: RenderJob) -> RenderJobResponse:
    attempts = []
    for attempt in sorted(job.attempts, key=lambda value: value.attempt_number, reverse=True):
        clip_results = [
            RenderClipResultResponse(
                id=result.id,
                clipId=result.clip_id,
                clipName=result.clip.original_name if result.clip else "Deleted clip",
                clipOrderIndex=result.clip_order_index,
                status=result.status,
                reasonCode=result.reason_code,
                reasonSummary=result.reason_summary,
                technicalDetail=result.technical_detail,
                providerMessage=result.provider_message,
            )
            for result in sorted(attempt.clip_results, key=lambda r: r.clip_order_index)
        ]
        attempts.append(
            RenderAttemptResponse(
                id=attempt.id,
                attemptNumber=attempt.attempt_number,
                status=attempt.status,
                failureStage=attempt.failure_stage,
                errorCategory=attempt.error_category,
                errorCode=attempt.error_code,
                providerErrorCode=attempt.provider_error_code,
                errorSummary=attempt.error_summary,
                errorDetail=attempt.error_detail,
                createdAt=attempt.created_at,
                completedAt=attempt.completed_at,
                clipResults=clip_results,
            )
        )

    return RenderJobResponse(
        id=job.id,
        movieId=job.movie_id,
        status=job.status,
        finalStatusReason=job.final_status_reason,
        acceptedClipCount=job.accepted_clip_count,
        failedClipCount=job.failed_clip_count,
        hasClipLevelFailures=job.has_clip_level_failures,
        attempts=attempts,
    )


@router.get("", response_model=list[MovieSummaryResponse])
def list_movies(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    movies = db.scalars(
        select(Movie)
        .where(Movie.user_id == user.id)
        .options(selectinload(Movie.slots))
        .order_by(Movie.updated_at.desc())
    ).all()
    responses: list[MovieSummaryResponse] = []
    for movie in movies:
        latest_video = db.get(Video, movie.latest_video_id) if movie.latest_video_id else None
        responses.append(
            MovieSummaryResponse(
                id=movie.id,
                title=movie.title,
                status=movie.status,
                updatedAt=movie.updated_at,
                slotCount=len(movie.slots),
                latestVideo=_video_summary(latest_video),
            )
        )
    return responses


@router.post("", response_model=MovieSummaryResponse)
def create_movie(
    payload: MovieCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    movie = Movie(user_id=user.id, title=payload.title, status="draft")
    db.add(movie)
    db.flush()

    db.commit()
    db.refresh(movie)
    return MovieSummaryResponse(
        id=movie.id,
        title=movie.title,
        status=movie.status,
        updatedAt=movie.updated_at,
        slotCount=0,
        latestVideo=None,
    )


@router.get("/{movie_id}", response_model=MovieDetailResponse)
def get_movie(
    movie_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    movie = db.scalar(
        select(Movie)
        .where(Movie.id == movie_id, Movie.user_id == user.id)
        .options(selectinload(Movie.slots))
    )
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found.")

    latest_video = db.get(Video, movie.latest_video_id) if movie.latest_video_id else None
    clips = db.scalars(
        select(Clip).where(Clip.user_id == user.id).order_by(Clip.created_at.desc())
    ).all()
    # Without this the editor is handed initialJob=null and an in-flight render
    # becomes invisible the moment the page is reloaded.
    latest_job = db.scalar(
        select(RenderJob)
        .where(RenderJob.movie_id == movie.id)
        .options(
            selectinload(RenderJob.attempts)
            .selectinload(RenderJobAttempt.clip_results)
            .selectinload(RenderJobClipResult.clip)
        )
        .order_by(RenderJob.created_at.desc())
    )
    return MovieDetailResponse(
        id=movie.id,
        title=movie.title,
        status=movie.status,
        updatedAt=movie.updated_at,
        slotCount=len(movie.slots),
        latestVideo=_video_summary(latest_video),
        clips=[_clip_response(clip) for clip in clips],
        slots=[_slot_response(slot) for slot in sorted(movie.slots, key=lambda item: item.sort_index)],
        latestJob=_job_response(latest_job) if latest_job else None,
    )


@router.put("/{movie_id}/timeline", response_model=MovieDetailResponse)
def update_timeline(
    movie_id: str,
    payload: MovieTimelineUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    movie = db.scalar(
        select(Movie)
        .where(Movie.id == movie_id, Movie.user_id == user.id)
        .options(selectinload(Movie.slots))
    )
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found.")

    active = db.scalar(
        select(RenderJob).where(
            RenderJob.movie_id == movie.id,
            RenderJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail="A render is already queued or running for this movie.",
        )

    existing_by_id = {slot.id: slot for slot in movie.slots}
    seen_existing_ids: set[str] = set()
    for slot_payload in payload.slots:
        slot_id = slot_payload.id
        if slot_id is not None and slot_id in existing_by_id:
            if slot_id in seen_existing_ids:
                raise HTTPException(status_code=400, detail="A timeline slot ID cannot appear more than once.")
            seen_existing_ids.add(slot_id)

    sort_indices = [slot.sort_index for slot in payload.slots]
    if len(sort_indices) != len(set(sort_indices)):
        raise HTTPException(status_code=400, detail="Timeline slots must have distinct sort indices.")

    movie.status = "editing"
    retained_ids: set[str] = set()
    assigned_clip_ids: set[str] = set()

    for slot_payload in payload.slots:
        if slot_payload.clip_id:
            clip = db.scalar(select(Clip).where(Clip.id == slot_payload.clip_id, Clip.user_id == user.id))
            if clip is None:
                raise HTTPException(status_code=400, detail="One selected clip is not available to this user.")
            if slot_payload.clip_id in assigned_clip_ids:
                raise HTTPException(status_code=400, detail="A clip cannot be assigned to multiple timeline positions.")
            assigned_clip_ids.add(slot_payload.clip_id)
        slot = existing_by_id.get(slot_payload.id or "")
        if slot is None:
            # Populate before flushing: sort_index is NOT NULL, so flushing a
            # bare MovieSlot and assigning afterwards fails the constraint and
            # turns every newly added slot into a 500.
            slot = MovieSlot(
                movie_id=movie.id,
                sort_index=slot_payload.sort_index,
                clip_id=slot_payload.clip_id,
                overlay_text=slot_payload.overlay_text,
            )
            db.add(slot)
            db.flush()
        else:
            slot.sort_index = slot_payload.sort_index
            slot.clip_id = slot_payload.clip_id
            slot.overlay_text = slot_payload.overlay_text
        retained_ids.add(slot.id)

    removed = [slot for slot in movie.slots if slot.id not in retained_ids]
    if removed:
        # Detach the render diagnostics first. RenderJobClipResult.movie_slot_id
        # has no relationship back to MovieSlot, so SQLAlchemy will not null it
        # for us and the DELETE is rejected on any movie that has been rendered.
        db.execute(
            update(RenderJobClipResult)
            .where(RenderJobClipResult.movie_slot_id.in_([slot.id for slot in removed]))
            .values(movie_slot_id=None)
        )
        for slot in removed:
            db.delete(slot)

    db.commit()
    return get_movie(movie_id, db, user)


@router.post("/{movie_id}/render", response_model=RenderJobResponse)
def queue_render(
    movie_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    movie = db.scalar(
        select(Movie)
        .where(Movie.id == movie_id, Movie.user_id == user.id)
        .options(selectinload(Movie.slots))
    )
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found.")

    active = db.scalar(
        select(RenderJob).where(
            RenderJob.movie_id == movie.id,
            RenderJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail="A render is already queued or running for this movie.",
        )

    selected_slots = [slot for slot in sorted(movie.slots, key=lambda value: value.sort_index) if slot.clip_id]
    if len(selected_slots) < settings.min_clip_count:
        raise HTTPException(status_code=400, detail=f"Select at least {settings.min_clip_count} clips before rendering.")
    if len(selected_slots) > settings.render_max_graph_inputs:
        # The worker enforces this too, but discovering it after queueing means
        # the user waits for a render that was never going to run.
        raise HTTPException(
            status_code=400,
            detail=(
                f"This movie has {len(selected_slots)} clips; the renderer joins at most "
                f"{settings.render_max_graph_inputs} in one pass."
            ),
        )
    clips = [db.get(Clip, slot.clip_id) for slot in selected_slots]
    if any(clip is None or not clip.team or not clip.player or not clip.tier for clip in clips):
        raise HTTPException(status_code=400, detail="Every selected clip must have valid filename metadata.")

    # Reuse the movie's existing job so retries accumulate as attempts, which
    # is what the job -> attempt -> clip-result hierarchy exists to record.
    job = db.scalar(
        select(RenderJob)
        .where(RenderJob.movie_id == movie.id)
        .options(selectinload(RenderJob.attempts))
        .order_by(RenderJob.created_at.desc())
    )
    if job is None:
        job = RenderJob(movie_id=movie.id)
        db.add(job)
    job.status = "queued"
    job.final_status_reason = "Waiting for worker pickup."
    job.accepted_clip_count = 0
    job.failed_clip_count = 0
    job.has_clip_level_failures = False
    job.heartbeat_at = None
    db.flush()

    next_number = max((a.attempt_number for a in job.attempts), default=0) + 1
    attempt = RenderJobAttempt(
        render_job_id=job.id,
        attempt_number=next_number,
        status="queued",
        provider="ffmpeg",
        request_payload_json={
            "movie_id": movie.id,
            "slot_count": len(selected_slots),
            "clip_order": [clip.original_name for clip in clips if clip],
            "overlay_format": "three-line bottom-left label",
        },
    )
    db.add(attempt)
    db.flush()
    job.latest_attempt_id = attempt.id
    movie.status = "queued"
    db.commit()
    db.refresh(job)
    db.refresh(attempt)
    return _job_response(job)

def _unlink(path_value: str | None) -> None:
    """Best-effort file removal. A failure here leaves an orphaned file, which
    the maintenance audit reports; it must never fail the request."""
    if not path_value:
        return
    try:
        Path(path_value).unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove %s", path_value, exc_info=True)


@router.delete("/{movie_id}", status_code=204)
def delete_movie(
    movie_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a movie, its timeline, its render history and its outputs.

    Order matters and is not enforced by the schema: Video.movie_id references
    movies and Movie.latest_video_id references videos, and videos also cite
    render attempts that the movie's ORM cascade deletes. Unwinding in the
    wrong order raises IntegrityError on MySQL.
    """
    movie = db.scalar(
        select(Movie).where(Movie.id == movie_id, Movie.user_id == user.id)
    )
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found.")

    active = db.scalar(
        select(RenderJob).where(
            RenderJob.movie_id == movie.id,
            RenderJob.status.in_(["queued", "running"]),
        )
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail="A render is in progress for this movie. Wait for it to finish.",
        )

    videos = db.scalars(select(Video).where(Video.movie_id == movie.id)).all()
    paths = [video.storage_path for video in videos]

    movie.latest_video_id = None
    # render_jobs.latest_attempt_id is a bare foreign key to the attempts the
    # ORM cascade is about to delete; clear it first or the cascade fails.
    jobs = db.scalars(select(RenderJob).where(RenderJob.movie_id == movie.id)).all()
    for job in jobs:
        job.latest_attempt_id = None
    db.flush()
    for video in videos:
        db.delete(video)
    db.flush()
    db.delete(movie)
    db.commit()

    for path in paths:
        _unlink(path)
    logger.info("deleted movie movie_id=%s videos=%d", movie_id, len(paths))
    return None
