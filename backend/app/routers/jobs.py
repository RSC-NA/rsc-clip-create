from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.database import get_db
from backend.app.deps import get_current_user
from backend.app.models import Movie, RenderJob, RenderJobAttempt, RenderJobClipResult, User
from backend.app.routers.movies import _job_response


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    job = db.scalar(
        select(RenderJob)
        .join(Movie, Movie.id == RenderJob.movie_id)
        .where(RenderJob.id == job_id, Movie.user_id == user.id)
        .options(
            selectinload(RenderJob.attempts)
            .selectinload(RenderJobAttempt.clip_results)
            .selectinload(RenderJobClipResult.clip),
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found.")
    return _job_response(job)
