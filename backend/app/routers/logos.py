from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import get_current_user
from backend.app.filename_parser import normalize_team
from backend.app.models import Logo, User
from backend.app.schemas import LogoResponse
from backend.app.storage import UploadTooLarge, safe_upload_name, save_upload


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/logos", tags=["logos"])
settings = get_settings()

SUPPORTED_LOGO_FORMATS = {"PNG", "JPEG", "WEBP", "GIF"}


def _to_response(logo: Logo) -> LogoResponse:
    return LogoResponse(
        id=logo.id,
        team=logo.team,
        originalName=logo.original_name,
        storagePath=logo.storage_path,
        imageUrl=f"{settings.fastapi_public_url}/api/v1/logos/{logo.id}/file",
        createdAt=logo.created_at,
    )


@router.get("", response_model=list[LogoResponse])
def list_logos(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    logos = db.scalars(
        select(Logo).where(Logo.user_id == user.id).order_by(Logo.team_normalized)
    ).all()
    return [_to_response(logo) for logo in logos]


@router.post("", response_model=LogoResponse)
def upload_logo(
    team: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload or replace the logo for one team.

    Matching is by the normalized team name, so re-uploading for the same team
    replaces the existing logo rather than creating a second one.
    """
    normalized = normalize_team(team)
    if not normalized:
        raise HTTPException(status_code=400, detail="A team name is required.")

    try:
        destination = save_upload(
            file,
            safe_upload_name(str(uuid.uuid4()), file.filename, "logo.png"),
            folder="logos",
            max_bytes=settings.max_logo_size_bytes,
        )
    except UploadTooLarge as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Verify it is genuinely an image, not just a trusted extension.
    try:
        with Image.open(destination) as probe:
            image_format = probe.format
            probe.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="That file is not a readable image.") from exc

    if image_format not in SUPPORTED_LOGO_FORMATS:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image format {image_format}. Use PNG, JPEG, WEBP, or GIF.",
        )

    existing = db.scalar(
        select(Logo).where(Logo.user_id == user.id, Logo.team_normalized == normalized)
    )
    if existing is not None:
        previous = existing.storage_path
        existing.team = team.strip()
        existing.original_name = file.filename or destination.name
        existing.storage_path = str(destination)
        db.commit()
        db.refresh(existing)
        if previous != str(destination):
            from pathlib import Path as _Path

            _Path(previous).unlink(missing_ok=True)
        logger.info("replaced logo for team=%s user=%s", normalized, user.id)
        return _to_response(existing)

    logo = Logo(
        user_id=user.id,
        team=team.strip(),
        team_normalized=normalized,
        original_name=file.filename or destination.name,
        storage_path=str(destination),
    )
    db.add(logo)
    db.commit()
    db.refresh(logo)
    logger.info("stored logo for team=%s user=%s", normalized, user.id)
    return _to_response(logo)


@router.get("/{logo_id}/file")
def get_logo_file(
    logo_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    from pathlib import Path

    from fastapi.responses import FileResponse

    logo = db.scalar(select(Logo).where(Logo.id == logo_id, Logo.user_id == user.id))
    if logo is None:
        raise HTTPException(status_code=404, detail="Logo not found.")
    path = Path(logo.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Logo file is missing.")
    return FileResponse(path)


@router.delete("/{logo_id}", status_code=204)
def delete_logo(
    logo_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    from pathlib import Path

    logo = db.scalar(select(Logo).where(Logo.id == logo_id, Logo.user_id == user.id))
    if logo is None:
        raise HTTPException(status_code=404, detail="Logo not found.")
    path = Path(logo.storage_path)
    db.delete(logo)
    db.commit()
    path.unlink(missing_ok=True)
    return None


def resolve_logo_path(db: Session, user_id: str, team: str | None):
    """Look up the stored logo for a clip's team. Never raises."""
    from pathlib import Path

    normalized = normalize_team(team)
    if not normalized:
        return None
    logo = db.scalar(
        select(Logo).where(Logo.user_id == user_id, Logo.team_normalized == normalized)
    )
    if logo is None:
        return None
    path = Path(logo.storage_path)
    if not path.is_file():
        logger.warning("logo row %s points at a missing file %s", logo.id, path)
        return None
    return path
