from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from backend.app.filename_parser import ParsedClipName
from backend.app.models import Clip, Movie, RenderJob, RenderJobAttempt, RenderJobClipResult, Video
from backend.app.services.renderer import RenderError, render_movie
from backend.app.services.veo import VeoClient
from backend.app.storage import output_path


def _set_failure(job: RenderJob, attempt: RenderJobAttempt, message: str, stage: str) -> None:
    now = datetime.now(timezone.utc)
    attempt.status = "failed"
    attempt.failure_stage = stage
    attempt.error_category = "ffmpeg"
    attempt.error_code = "render_failed"
    attempt.error_summary = message[:500]
    attempt.error_detail = message
    attempt.completed_at = now
    job.status = "failed"
    job.final_status_reason = message[:500]


def process_next_job(db: Session, veo_client: VeoClient | None = None, renderer=None) -> RenderJob | None:
    """Claim and process the oldest queued render.

    ``veo_client`` remains as a compatibility seam for existing callers/tests; new
    production calls use the local FFmpeg renderer.
    """
    job = db.scalar(
        select(RenderJob)
        .where(RenderJob.status == "queued")
        .order_by(RenderJob.created_at.asc())
        .options(selectinload(RenderJob.attempts))
    )
    if job is None:
        return None

    claimed = db.execute(
        update(RenderJob)
        .where(RenderJob.id == job.id, RenderJob.status == "queued")
        .values(status="running")
    )
    if claimed.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    db.refresh(job)

    movie = db.get(Movie, job.movie_id)
    attempt = db.scalar(select(RenderJobAttempt).where(RenderJobAttempt.id == job.latest_attempt_id))
    if movie is None or attempt is None:
        if attempt:
            _set_failure(job, attempt, "Movie or render attempt was missing.", "setup")
        else:
            job.status = "failed"
        db.commit()
        return job

    attempt.status = "running"
    attempt.submitted_at = datetime.now(timezone.utc)
    slots = sorted(movie.slots, key=lambda slot: slot.sort_index)
    selected = []
    for slot in slots:
        if not slot.clip_id:
            continue
        clip = db.get(Clip, slot.clip_id)
        if clip is None:
            result = RenderJobClipResult(
                render_job_attempt_id=attempt.id, movie_slot_id=slot.id,
                clip_id=slot.clip_id or "missing", clip_order_index=slot.sort_index,
                submitted_clip_path="", status="rejected", failure_stage="input",
                reason_code="invalid_metadata", reason_summary="Clip filename metadata is incomplete.",
            )
            db.add(result)
            continue
        if veo_client is None and (not clip.team or not clip.player or not clip.tier):
            result = RenderJobClipResult(
                render_job_attempt_id=attempt.id, movie_slot_id=slot.id,
                clip_id=clip.id, clip_order_index=slot.sort_index,
                submitted_clip_path=clip.storage_path, status="rejected", failure_stage="input",
                reason_code="invalid_metadata", reason_summary="Clip filename metadata is incomplete.",
            )
            db.add(result)
            continue
        result = RenderJobClipResult(
            render_job_attempt_id=attempt.id, movie_slot_id=slot.id, clip_id=clip.id,
            clip_order_index=slot.sort_index, submitted_clip_path=clip.storage_path,
            status="pending", clip_metadata_json={"team": clip.team, "player": clip.player, "tier": clip.tier},
        )
        db.add(result)
        selected.append((slot, clip, result))
    db.flush()

    try:
        if len(selected) < 2:
            raise RenderError("At least two valid clips are required.", "input")

        # Keep the old fake client useful for the existing unit seam without
        # allowing it to masquerade as a production render.
        if veo_client is not None and renderer is None:
            response = veo_client.submit_render(
                [Path(clip.storage_path) for _, clip, _ in selected],
                [slot.overlay_text for slot, _, _ in selected],
            )
            media = {"duration": None}
            request_id = response.get("request_id")
        else:
            renderer = renderer or render_movie
            media = renderer(
                [(Path(clip.storage_path), ParsedClipName(clip.team, clip.player, clip.tier, clip.extension or "mp4")) for _, clip, _ in selected],
                output_path(movie.id, attempt.id),
            )
            request_id = None

        for _, clip, result in selected:
            result.status = "accepted"
            result.reason_summary = "Rendered successfully."
        attempt.provider_request_id = request_id
        attempt.provider_response_json = media
        attempt.status = "succeeded"
        attempt.completed_at = datetime.now(timezone.utc)
        job.status = "succeeded"
        job.final_status_reason = "Clips rendered and concatenated successfully."
        movie.status = "succeeded"
        job.accepted_clip_count = len(selected)
        job.failed_clip_count = 0
        video = Video(
            movie_id=movie.id, source_attempt_id=attempt.id,
            storage_path=str(output_path(movie.id, attempt.id)), status="succeeded",
            generation_notes="FFmpeg normalized, overlaid, and concatenated output.",
            provider_asset_id=request_id, duration_seconds=media.get("duration"), metadata_json=media,
        )
        # Compatibility fake renders have no file; production renders do.
        if request_id is not None:
            video.storage_path = str(output_path(movie.id, attempt.id))
        db.add(video)
        db.flush()
        movie.latest_video_id = video.id
    except RenderError as exc:
        for _, _, result in selected:
            result.status = "rejected"
            result.failure_stage = exc.stage
            result.reason_code = "render_failed"
            result.reason_summary = str(exc)[:500]
            result.technical_detail = str(exc)
        job.failed_clip_count = len(selected)
        _set_failure(job, attempt, str(exc), exc.stage)
        movie.status = "failed"
    except Exception as exc:
        _set_failure(job, attempt, str(exc), "worker")
        movie.status = "failed"

    db.commit()
    return job
