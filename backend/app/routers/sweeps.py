from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import get_current_user
from backend.app.models import BrandSweep, User
from backend.app.schemas import BrandSweepResponse
from backend.app.services import brand
from backend.app.storage import UploadTooLarge, probe_media, safe_upload_name, save_upload


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sweeps", tags=["sweeps"])
settings = get_settings()


def _to_response(sweep: BrandSweep) -> BrandSweepResponse:
    return BrandSweepResponse(
        id=sweep.id,
        originalName=sweep.original_name,
        storagePath=sweep.storage_path,
        videoUrl=f"{settings.fastapi_public_url}/api/v1/sweeps/{sweep.id}/file",
        durationSeconds=sweep.duration_seconds,
        createdAt=sweep.created_at,
    )


def _current(db: Session, user_id: str) -> BrandSweep | None:
    return db.scalar(select(BrandSweep).where(BrandSweep.user_id == user_id))


def _has_alpha(pix_fmt: str | None) -> bool:
    """Only accept ffprobe pixel formats with an explicit alpha component.

    A container or codec name alone cannot establish transparency (e.g. a
    .mov may contain opaque H.264). Unknown formats fail closed.
    """
    if not pix_fmt:
        return False
    return pix_fmt.startswith(("yuva", "gbrap", "rgba", "bgra", "ayuv", "ya16")) or pix_fmt in {
        "argb", "abgr", "ya8", "vuya",
    }


@router.get("", response_model=BrandSweepResponse | None)
def get_brand_sweep(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The caller's own sweep, or null if they are using the bundled one."""
    sweep = _current(db, user.id)
    return _to_response(sweep) if sweep else None


@router.post("", response_model=BrandSweepResponse)
def upload_brand_sweep(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set or replace the sweep played over every cut."""
    extension = Path(file.filename or "").suffix.lower().lstrip(".")
    if extension not in settings.supported_sweep_formats:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported sweep format {extension or 'unknown'!s}. Use "
                f"{', '.join(sorted(settings.supported_sweep_formats))}."
            ),
        )

    try:
        destination = save_upload(
            file,
            safe_upload_name(str(uuid.uuid4()), file.filename, "brand-sweep.mov"),
            folder="sweeps",
            max_bytes=settings.max_brand_sweep_bytes,
        )
    except UploadTooLarge as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Verify it is genuinely decodable video, not just a trusted extension.
    # An unreadable sweep would otherwise be discovered by the worker, one
    # render at a time, long after the upload appeared to succeed.
    media = probe_media(destination)
    duration = media.get("duration")
    if not media.get("has_video") or not duration or duration <= 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400, detail="That file is not a readable video."
        )

    if not _has_alpha(media.get("pix_fmt")):
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The sweep video must have an alpha channel.")

    existing = _current(db, user.id)
    if existing is not None:
        previous = existing.storage_path
        existing.original_name = file.filename or destination.name
        existing.storage_path = str(destination)
        existing.duration_seconds = duration
        db.commit()
        db.refresh(existing)
        if previous != str(destination):
            Path(previous).unlink(missing_ok=True)
        logger.info("replaced brand sweep user=%s", user.id)
        return _to_response(existing)

    sweep = BrandSweep(
        user_id=user.id,
        original_name=file.filename or destination.name,
        storage_path=str(destination),
        duration_seconds=duration,
    )
    db.add(sweep)
    db.commit()
    db.refresh(sweep)
    logger.info("stored brand sweep user=%s", user.id)
    return _to_response(sweep)


@router.get("/{sweep_id}/file")
def get_sweep_file(
    sweep_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    sweep = db.scalar(
        select(BrandSweep).where(BrandSweep.id == sweep_id, BrandSweep.user_id == user.id)
    )
    if sweep is None:
        raise HTTPException(status_code=404, detail="Brand sweep not found.")
    path = Path(sweep.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Brand sweep file is missing.")
    return FileResponse(path)


@router.delete("/{sweep_id}", status_code=204)
def delete_sweep(
    sweep_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Drop the uploaded sweep. Renders fall back to the bundled one."""
    sweep = db.scalar(
        select(BrandSweep).where(BrandSweep.id == sweep_id, BrandSweep.user_id == user.id)
    )
    if sweep is None:
        raise HTTPException(status_code=404, detail="Brand sweep not found.")
    path = Path(sweep.storage_path)
    db.delete(sweep)
    db.commit()
    path.unlink(missing_ok=True)
    return None


def resolve_brand_sweep(db: Session, user_id: str) -> Path | None:
    """The sweep to play over this account's cuts. Never raises.

    ``None`` means "no account-specific sweep", which the renderer reads as the
    bundled one -- not as "no transition". A row pointing at a file that has
    gone missing is the same case: fall back rather than fail the render.
    """
    sweep = _current(db, user_id)
    if sweep is None:
        return None
    path = Path(sweep.storage_path)
    if not path.is_file():
        logger.warning("brand sweep row %s points at a missing file %s", sweep.id, path)
        return None
    return path
