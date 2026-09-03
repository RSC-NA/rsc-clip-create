from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from backend.app.config import get_settings
from backend.app.filename_parser import ParsedClipName
from backend.app.models import Clip, Movie, RenderJob, RenderJobAttempt, RenderJobClipResult, Video
from backend.app.routers.logos import resolve_logo_path
from backend.app.services import brand
from backend.app.services.renderer import ClipSpec, RenderError, RenderOptions, render_movie
from backend.app.storage import output_path


logger = logging.getLogger(__name__)
settings = get_settings()


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


def process_next_job(db: Session, renderer=None) -> RenderJob | None:
    """Claim and process the oldest queued render.

    ``renderer`` is an injection seam for tests; production uses ``render_movie``.
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
        .values(status="running", heartbeat_at=datetime.now(timezone.utc))
    )
    if claimed.rowcount != 1:
        # Another worker won the race for this job.
        db.rollback()
        return None
    db.commit()
    db.refresh(job)
    logger.info("claimed render job job_id=%s movie_id=%s", job.id, job.movie_id)

    movie = db.get(Movie, job.movie_id)
    attempt = db.scalar(select(RenderJobAttempt).where(RenderJobAttempt.id == job.latest_attempt_id))
    if movie is None or attempt is None:
        logger.error("render job is missing its movie or attempt job_id=%s", job.id)
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
            # The slot points at a clip row that no longer exists. Record the
            # rejection against the slot; clip_id is left unset because it would
            # not satisfy the foreign key.
            result = RenderJobClipResult(
                render_job_attempt_id=attempt.id, movie_slot_id=slot.id,
                clip_id=None, clip_order_index=slot.sort_index,
                submitted_clip_path="", status="rejected", failure_stage="input",
                reason_code="missing_clip", reason_summary="The selected clip no longer exists.",
            )
            db.add(result)
            continue
        if not clip.team or not clip.player or not clip.tier:
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

    started = datetime.now(timezone.utc)
    try:
        if len(selected) < settings.min_clip_count:
            raise RenderError(
                f"At least {settings.min_clip_count} valid clips are required.", "input"
            )

        destination = output_path(movie.id, attempt.id)
        render = renderer or render_movie
        logger.info(
            "rendering job_id=%s attempt_id=%s clips=%d -> %s",
            job.id, attempt.id, len(selected), destination,
        )
        hold_seconds = (
            movie.transition_hold_seconds
            if movie.transition_hold_seconds is not None
            else settings.render_sting_hold_seconds
        )
        options = RenderOptions(
            movie_id=movie.id,
            fps=settings.render_fps,
            width=settings.render_width,
            height=settings.render_height,
            hold_frames=max(0, round(hold_seconds * settings.render_fps)),
            blend_frames=settings.render_blend_frames,
            family=movie.transition_style or settings.render_transition_style,
            wordmark=movie.title,
            font_path=settings.render_font_path,
            font_size=settings.render_font_size,
            logo_max_width=settings.render_logo_max_width,
            blue=settings.render_brand_blue,
            orange=settings.render_brand_orange,
        )
        media = render(
            [
                ClipSpec(
                    source=Path(clip.storage_path),
                    name=ParsedClipName(
                        clip.team, clip.player, clip.tier, clip.extension or "mp4"
                    ),
                    logo_path=resolve_logo_path(db, movie.user_id, clip.team),
                )
                for _, clip, _ in selected
            ],
            destination,
            options,
        )

        for _, _, result in selected:
            result.status = "accepted"
            result.reason_summary = "Rendered successfully."
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
            storage_path=str(destination), status="succeeded",
            generation_notes="FFmpeg normalized, overlaid, and concatenated output.",
            duration_seconds=media.get("duration"), metadata_json=media,
        )
        db.add(video)
        db.flush()
        movie.latest_video_id = video.id
        logger.info(
            "render succeeded job_id=%s video_id=%s duration=%.3fs elapsed=%.1fs",
            job.id, video.id, media.get("duration") or 0.0,
            (datetime.now(timezone.utc) - started).total_seconds(),
        )
    except RenderError as exc:
        logger.warning("render failed job_id=%s stage=%s: %s", job.id, exc.stage, exc)
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
        logger.exception("unexpected worker error job_id=%s", job.id)
        _set_failure(job, attempt, str(exc), "worker")
        movie.status = "failed"

    db.commit()
    return job


def requeue_stale_jobs(db: Session, *, timeout_seconds: int | None = None) -> int:
    """Return jobs abandoned by a crashed worker to the queue.

    A worker that dies mid-render leaves its job in ``running`` forever, and the
    editor polls it indefinitely. Anything still running well past the ffmpeg
    timeout cannot be alive.
    """
    timeout = timeout_seconds or (settings.render_ffmpeg_timeout_seconds * 2)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout)
    stale = db.scalars(
        select(RenderJob).where(RenderJob.status == "running")
    ).all()

    requeued = 0
    for job in stale:
        beat = job.heartbeat_at
        if beat is not None and beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        if beat is not None and beat > cutoff:
            continue
        job.status = "queued"
        job.final_status_reason = "Requeued after the previous worker stopped responding."
        requeued += 1
        logger.warning("requeued stale render job job_id=%s", job.id)
    if requeued:
        db.commit()
    return requeued
