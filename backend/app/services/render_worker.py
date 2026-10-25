from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from backend.app.config import get_settings
from backend.app.filename_parser import ParsedClipName
from backend.app.models import Clip, Movie, RenderJob, RenderJobAttempt, RenderJobClipResult, Video
from backend.app.routers.sweeps import resolve_brand_sweep
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


def _discard_output(movie_id: str, attempt_id: str) -> None:
    """Best-effort removal of this attempt's rendered MP4.

    Called when the render outcome cannot be recorded as a success, so storage
    never holds a finished file that no Video row references. Never raises.
    """
    try:
        output_path(movie_id, attempt_id).unlink(missing_ok=True)
    except Exception:
        logger.warning(
            "could not remove unrecorded render output movie_id=%s attempt_id=%s",
            movie_id, attempt_id, exc_info=True,
        )


def process_next_job(db: Session, renderer=None) -> RenderJob | None:
    """Claim and process the oldest queued render.

    Retries reuse the movie's RenderJob row, so RenderJob.created_at is the time
    of the movie's first render. Queue order is therefore taken from the job's
    latest attempt, falling back to the job row for a job with no attempt.

    ``renderer`` is an injection seam for tests; production uses ``render_movie``.
    """
    queued_at = func.coalesce(RenderJobAttempt.created_at, RenderJob.created_at)
    job = db.scalar(
        select(RenderJob)
        .outerjoin(RenderJobAttempt, RenderJobAttempt.id == RenderJob.latest_attempt_id)
        .where(RenderJob.status == "queued")
        .order_by(queued_at.asc(), RenderJob.created_at.asc())
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

    # A crashed worker may have committed its pending diagnostics in a progress
    # heartbeat. A stale requeue reuses this attempt, so replace those rows
    # before recording diagnostics for the new claim (including rejections).
    db.execute(
        delete(RenderJobClipResult).where(RenderJobClipResult.render_job_attempt_id == attempt.id)
    )
    attempt.status = "running"
    attempt.submitted_at = datetime.now(timezone.utc)
    slots = sorted(movie.slots, key=lambda slot: slot.sort_index)
    selected = []
    rejected_count = 0
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
            rejected_count += 1
            continue
        if not clip.team or not clip.player or not clip.tier:
            result = RenderJobClipResult(
                render_job_attempt_id=attempt.id, movie_slot_id=slot.id,
                clip_id=clip.id, clip_order_index=slot.sort_index,
                submitted_clip_path=clip.storage_path, status="rejected", failure_stage="input",
                reason_code="invalid_metadata", reason_summary="Clip filename metadata is incomplete.",
            )
            db.add(result)
            rejected_count += 1
            continue
        result = RenderJobClipResult(
            render_job_attempt_id=attempt.id, movie_slot_id=slot.id, clip_id=clip.id,
            clip_order_index=slot.sort_index, submitted_clip_path=clip.storage_path,
            status="pending", clip_metadata_json={"team": clip.team, "player": clip.player, "tier": clip.tier},
        )
        db.add(result)
        selected.append((slot, clip, result))

    started = datetime.now(timezone.utc)
    # Plain values, so cleanup never depends on ORM state after a failed flush.
    output_movie_id, output_attempt_id = movie.id, attempt.id
    try:
        # Inside the try: status="running" was already committed at claim time,
        # so anything escaping from here leaves the job wedged in "running"
        # forever while the editor polls it.
        #
        # Commit, not just flush: the heartbeat callback rolls back on failure,
        # and that rollback must only undo the heartbeat UPDATE, never the
        # DELETE of stale rows or this claim's pending/rejected diagnostics.
        # The committed objects stay attached and refresh on access.
        db.commit()
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
        options = RenderOptions(
            movie_id=movie.id,
            fps=settings.render_fps,
            width=settings.render_width,
            height=settings.render_height,
            # The account's own sweep wins, then the configured override, then
            # the sweep bundled in the image. None at every step is fine: the
            # renderer reads it as "use the bundled one".
            sweep_path=(
                resolve_brand_sweep(db, movie.user_id)
                or settings.render_brand_sweep_path
            ),
            font_path=settings.render_font_path,
            font_size=settings.render_font_size,
            stroke_width=settings.render_label_stroke_width,
        )
        def touch_heartbeat() -> None:
            # heartbeat_at was written once at claim time and never again, so a
            # render legitimately longer than the stale threshold was declared
            # dead *while running* and a second worker picked it up.
            try:
                db.execute(
                    update(RenderJob)
                    .where(RenderJob.id == job.id)
                    .values(heartbeat_at=datetime.now(timezone.utc))
                )
                db.commit()
            except Exception:
                logger.warning("heartbeat update failed job_id=%s", job.id, exc_info=True)
                db.rollback()

        media = render(
            [
                ClipSpec(
                    source=Path(clip.storage_path),
                    name=ParsedClipName(
                        clip.team, clip.player, clip.tier, clip.extension or "mp4"
                    ),
                )
                for _, clip, _ in selected
            ],
            destination,
            options,
            on_progress=touch_heartbeat,
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
        job.failed_clip_count = rejected_count
        job.has_clip_level_failures = rejected_count > 0
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
        # Count every clip result marked rejected for this attempt, including
        # inputs rejected before rendering, as the success path does.
        job.failed_clip_count = len(selected) + rejected_count
        job.has_clip_level_failures = job.failed_clip_count > 0
        _set_failure(job, attempt, str(exc), exc.stage)
        movie.status = "failed"
    except Exception as exc:
        logger.exception("unexpected worker error job_id=%s", job.id)
        # Count inputs rejected before rendering. Selected clips stay "pending"
        # and are not counted as failures.
        job.failed_clip_count = rejected_count
        job.has_clip_level_failures = rejected_count > 0
        _set_failure(job, attempt, str(exc), "worker")
        movie.status = "failed"
        # The renderer may already have moved its output into place; with no
        # Video row recorded, nothing would ever reference that file.
        _discard_output(output_movie_id, output_attempt_id)

    # Captured before the commit: a rollback expires every ORM object, and
    # reading them afterwards would lazy-load outside any error guard.
    job_id, movie_id, attempt_id = job.id, movie.id, attempt.id
    try:
        db.commit()
    except Exception:
        # The render may have produced a file, but we could not record it.
        # Leaving the row at "running" would strand the job, so fail it in a
        # fresh transaction rather than letting the exception escape.
        logger.exception("could not record render outcome job_id=%s", job_id)
        db.rollback()
        _force_fail(
            db, job_id, "The render outcome could not be recorded.",
            movie_id=movie_id, attempt_id=attempt_id,
        )
        # The rollback discarded any Video row, so the file is unreferenced.
        _discard_output(movie_id, attempt_id)
    return job


def _force_fail(
    db: Session,
    job_id: str,
    message: str,
    *,
    movie_id: str | None = None,
    attempt_id: str | None = None,
) -> None:
    """Fail the job, its movie and its latest attempt in one transaction.

    Uses explicit UPDATEs so nothing depends on rolled-back in-memory state.
    Never raises: on any error it logs and rolls back.
    """
    try:
        if movie_id is None or attempt_id is None:
            row = db.execute(
                select(RenderJob.movie_id, RenderJob.latest_attempt_id).where(RenderJob.id == job_id)
            ).first()
            if row is not None:
                movie_id = movie_id or row.movie_id
                attempt_id = attempt_id or row.latest_attempt_id
        db.execute(
            update(RenderJob)
            .where(RenderJob.id == job_id)
            .values(status="failed", final_status_reason=message[:500])
        )
        if movie_id is not None:
            db.execute(update(Movie).where(Movie.id == movie_id).values(status="failed"))
        if attempt_id is not None:
            db.execute(
                update(RenderJobAttempt)
                .where(RenderJobAttempt.id == attempt_id)
                .values(
                    status="failed",
                    failure_stage="worker",
                    error_summary=message[:500],
                    error_detail=message,
                    completed_at=datetime.now(timezone.utc),
                )
            )
        db.commit()
    except Exception:
        logger.exception("could not mark job failed job_id=%s", job_id)
        db.rollback()


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
        # Guarded UPDATE, not an ORM mutation: a job that finished between the
        # SELECT above and this write must not be knocked back to "queued".
        result = db.execute(
            update(RenderJob)
            .where(RenderJob.id == job.id, RenderJob.status == "running")
            .values(
                status="queued",
                final_status_reason="Requeued after the previous worker stopped responding.",
            )
        )
        if result.rowcount == 1:
            requeued += 1
            logger.warning("requeued stale render job job_id=%s", job.id)
    if requeued:
        db.commit()
    else:
        db.rollback()
    return requeued
