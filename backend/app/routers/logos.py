from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import get_current_user
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
        originalName=logo.original_name,
        storagePath=logo.storage_path,
        imageUrl=f"{settings.fastapi_public_url}/api/v1/logos/{logo.id}/file",
        createdAt=logo.created_at,
    )


def _current(db: Session, user_id: str) -> Logo | None:
    return db.scalar(select(Logo).where(Logo.user_id == user_id))


@router.get("", response_model=LogoResponse | None)
def get_brand_logo(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The caller's brand logo, or null if none has been uploaded."""
    logo = _current(db, user.id)
    return _to_response(logo) if logo else None


@router.post("", response_model=LogoResponse)
def upload_brand_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set or replace the brand logo shown in the centre of every sting."""
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
            width, height = probe.size
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

    # Pillow's decompression-bomb guard only trips above ~178M pixels. A
    # 13000x13000 PNG sails under it at a few tens of KB on disk and then
    # decodes to hundreds of MB of RGBA per copy inside the render worker.
    limit = settings.max_logo_dimension
    if width > limit or height > limit:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=f"Logo is {width}x{height}; the maximum is {limit}x{limit}.",
        )

    existing = _current(db, user.id)
    if existing is not None:
        previous = existing.storage_path
        existing.original_name = file.filename or destination.name
        existing.storage_path = str(destination)
        db.commit()
        db.refresh(existing)
        if previous != str(destination):
            Path(previous).unlink(missing_ok=True)
        logger.info("replaced brand logo user=%s", user.id)
        return _to_response(existing)

    logo = Logo(
        user_id=user.id,
        original_name=file.filename or destination.name,
        storage_path=str(destination),
    )
    db.add(logo)
    db.commit()
    db.refresh(logo)
    logger.info("stored brand logo user=%s", user.id)
    return _to_response(logo)


@router.get("/{logo_id}/file")
def get_logo_file(
    logo_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
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
    logo = db.scalar(select(Logo).where(Logo.id == logo_id, Logo.user_id == user.id))
    if logo is None:
        raise HTTPException(status_code=404, detail="Logo not found.")
    path = Path(logo.storage_path)
    db.delete(logo)
    db.commit()
    path.unlink(missing_ok=True)
    return None


def resolve_brand_logo(db: Session, user_id: str) -> Path | None:
    """The logo to centre in each sting. Never raises.

    A missing row or a missing file degrades to a sting with no mark rather
    than failing the render.
    """
    logo = _current(db, user_id)
    if logo is None:
        return None
    path = Path(logo.storage_path)
    if not path.is_file():
        logger.warning("logo row %s points at a missing file %s", logo.id, path)
        return None
    return path
