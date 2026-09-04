import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import get_current_user
from backend.app.models import Movie, User, Video
from backend.app.schemas import VideoLocationResponse


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/videos", tags=["videos"])
settings = get_settings()


@router.get("/{video_id}/location", response_model=VideoLocationResponse)
def get_video_location(
    video_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")

    movie = db.get(Movie, video.movie_id)
    if movie is None or (movie.user_id != user.id and not user.is_admin):
        raise HTTPException(status_code=404, detail="Video not found.")

    return VideoLocationResponse(
        videoUuid=video.id,
        storagePath=video.storage_path,
        downloadUrl=f"{settings.fastapi_public_url}/api/v1/videos/{video.id}/file",
    )


@router.get("/{video_id}/file")
def stream_video(
    video_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    movie = db.get(Movie, video.movie_id)
    if movie is None or (movie.user_id != user.id and not user.is_admin):
        raise HTTPException(status_code=404, detail="Video not found.")
    path = Path(video.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Rendered video file is missing.")
    return FileResponse(path, media_type="video/mp4", filename=f"{movie.title}.mp4")


@router.delete("/{video_id}", status_code=204)
def delete_video(
    video_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Delete a rendered output and its file."""
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    movie = db.get(Movie, video.movie_id)
    if movie is None or movie.user_id != user.id:
        raise HTTPException(status_code=404, detail="Video not found.")

    # Movie.latest_video_id references videos; clear it before the delete.
    if movie.latest_video_id == video.id:
        movie.latest_video_id = None
        db.flush()

    path = video.storage_path
    db.delete(video)
    db.commit()

    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove %s", path, exc_info=True)
    logger.info("deleted video video_id=%s", video_id)
    return None
