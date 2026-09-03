from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from fastapi import APIRouter, Depends, HTTPException

from backend.app.database import get_db
from backend.app.config import get_settings
from backend.app.deps import get_current_user
from backend.app.models import Clip, Movie, MovieSlot, RenderJob, RenderJobAttempt, User, Video
from backend.app.services.brand import DEFAULT_FAMILY, TRANSITION_FAMILIES
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


router = APIRouter(prefix="/movies", tags=["movies"])
settings = get_settings()


def _style_of(movie: Movie) -> str:
    return movie.transition_style or settings.render_transition_style or DEFAULT_FAMILY


def _hold_of(movie: Movie) -> float:
    return (
        movie.transition_hold_seconds
        if movie.transition_hold_seconds is not None
        else settings.render_sting_hold_seconds
    )


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
            for result in attempt.clip_results
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
                transitionStyle=_style_of(movie),
                transitionHoldSeconds=_hold_of(movie),
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
        transitionStyle=_style_of(movie),
        transitionHoldSeconds=_hold_of(movie),
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
    return MovieDetailResponse(
        id=movie.id,
        title=movie.title,
        status=movie.status,
        updatedAt=movie.updated_at,
        slotCount=len(movie.slots),
        latestVideo=_video_summary(latest_video),
        clips=[_clip_response(clip) for clip in clips],
        slots=[_slot_response(slot) for slot in sorted(movie.slots, key=lambda item: item.sort_index)],
        transitionStyle=_style_of(movie),
        transitionHoldSeconds=_hold_of(movie),
        transitionStyles=sorted(TRANSITION_FAMILIES),
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

    if payload.transition_style is not None:
        if payload.transition_style not in TRANSITION_FAMILIES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown transition style {payload.transition_style!r}.",
            )
        movie.transition_style = payload.transition_style
    if payload.transition_hold_seconds is not None:
        movie.transition_hold_seconds = payload.transition_hold_seconds

    movie.status = "editing"
    existing_by_id = {slot.id: slot for slot in movie.slots}
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
            slot = MovieSlot(movie_id=movie.id)
            db.add(slot)
            db.flush()
        slot.sort_index = slot_payload.sort_index
        slot.clip_id = slot_payload.clip_id
        slot.overlay_text = slot_payload.overlay_text
        retained_ids.add(slot.id)

    for slot in list(movie.slots):
        if slot.id not in retained_ids:
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
    clips = [db.get(Clip, slot.clip_id) for slot in selected_slots]
    if any(clip is None or not clip.team or not clip.player or not clip.tier for clip in clips):
        raise HTTPException(status_code=400, detail="Every selected clip must have valid filename metadata.")

    job = RenderJob(
        movie_id=movie.id,
        status="queued",
        final_status_reason="Waiting for worker pickup.",
        accepted_clip_count=0,
        failed_clip_count=0,
    )
    db.add(job)
    db.flush()
    attempt = RenderJobAttempt(
        render_job_id=job.id,
        attempt_number=1,
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
    job.attempts = [attempt]
    return _job_response(job)
