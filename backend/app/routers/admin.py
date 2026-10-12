from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from fastapi import APIRouter, Depends

from backend.app.database import get_db
from backend.app.deps import get_admin_user
from backend.app.models import Movie, RenderJob, RenderJobAttempt, RenderJobClipResult, User, Video
from backend.app.schemas import (
    AdminDashboardResponse,
    AdminErrorResponse,
    AdminUserResponse,
    AdminVideoResponse,
    RenderClipResultResponse,
)


router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/dashboard", response_model=AdminDashboardResponse)
def admin_dashboard(
    db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    total_users = db.scalar(select(func.count()).select_from(User)) or 0
    total_movies = db.scalar(select(func.count()).select_from(Movie)) or 0
    total_videos = db.scalar(select(func.count()).select_from(Video)) or 0
    failed_attempts = db.scalar(
        select(func.count()).select_from(RenderJobAttempt).where(
            RenderJobAttempt.status.in_(["failed", "partial_failure", "provider_rejected"])
        )
    ) or 0

    return AdminDashboardResponse(
        totalUsers=total_users,
        totalMovies=total_movies,
        totalVideos=total_videos,
        failedAttempts=failed_attempts,
    )


@router.get("/users", response_model=list[AdminUserResponse])
def admin_users(
    db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    responses = []
    for user in users:
        movie_count = db.scalar(
            select(func.count()).select_from(Movie).where(Movie.user_id == user.id)
        ) or 0
        video_count = db.scalar(
            select(func.count())
            .select_from(Video)
            .join(Movie, Movie.id == Video.movie_id)
            .where(Movie.user_id == user.id)
        ) or 0
        responses.append(
            AdminUserResponse(
                id=user.id,
                displayName=user.display_name,
                email=user.email,
                discordUsername=user.discord_username,
                isAdmin=user.is_admin,
                movieCount=movie_count,
                videoCount=video_count,
                latestSignInAt=user.latest_sign_in_at,
            )
        )
    return responses


@router.get("/videos", response_model=list[AdminVideoResponse])
def admin_videos(
    db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    videos = db.scalars(select(Video).order_by(Video.created_at.desc())).all()
    responses = []
    for video in videos:
        movie = db.get(Movie, video.movie_id)
        owner = db.get(User, movie.user_id) if movie else None
        source_attempt = (
            db.get(RenderJobAttempt, video.source_attempt_id)
            if video.source_attempt_id
            else None
        )
        responses.append(
            AdminVideoResponse(
                id=video.id,
                movieTitle=movie.title if movie else "Unknown Movie",
                ownerName=owner.display_name if owner else "Unknown Owner",
                status=video.status,
                storagePath=video.storage_path,
                latestAttemptStatus=source_attempt.status if source_attempt else None,
                createdAt=video.created_at,
            )
        )
    return responses


@router.get("/errors", response_model=list[AdminErrorResponse])
def admin_errors(
    db: Session = Depends(get_db), _: User = Depends(get_admin_user)
):
    attempts = db.scalars(
        select(RenderJobAttempt)
        .where(
            or_(
                RenderJobAttempt.status.in_(
                    ["failed", "partial_failure", "provider_rejected"]
                ),
                (RenderJobAttempt.status == "succeeded")
                & RenderJobAttempt.clip_results.any(RenderJobClipResult.status == "rejected"),
            )
        )
        .options(
            selectinload(RenderJobAttempt.clip_results).selectinload(
                RenderJobClipResult.clip
            )
        )
        .order_by(RenderJobAttempt.created_at.desc())
    ).all()

    responses = []
    for attempt in attempts:
        job = db.get(RenderJob, attempt.render_job_id)
        movie = db.get(Movie, job.movie_id) if job else None
        owner = db.get(User, movie.user_id) if movie else None
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
        responses.append(
            AdminErrorResponse(
                jobId=job.id if job else "unknown",
                movieTitle=movie.title if movie else "Unknown Movie",
                ownerName=owner.display_name if owner else "Unknown Owner",
                attemptNumber=attempt.attempt_number,
                status=attempt.status,
                failureStage=attempt.failure_stage,
                errorCategory=attempt.error_category,
                errorSummary=attempt.error_summary,
                failedClipCount=sum(
                    result.status == "rejected" for result in attempt.clip_results
                ),
                createdAt=attempt.created_at,
                clipResults=clip_results,
            )
        )
    return responses
