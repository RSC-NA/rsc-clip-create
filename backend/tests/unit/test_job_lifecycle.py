import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from backend.app import worker
from backend.app.models import Movie, RenderJob, User
from backend.app.services.render_worker import requeue_stale_jobs


def _job(db, status="running", heartbeat=None):
    user = User(discord_user_id=str(uuid.uuid4()), display_name="U")
    db.add(user)
    db.flush()
    movie = Movie(user_id=user.id, title="M")
    db.add(movie)
    db.flush()
    job = RenderJob(movie_id=movie.id, status=status, heartbeat_at=heartbeat)
    db.add(job)
    db.commit()
    return job


def test_a_job_abandoned_by_a_dead_worker_is_requeued(db_session):
    stale = datetime.now(timezone.utc) - timedelta(hours=48)
    job = _job(db_session, "running", stale)

    assert requeue_stale_jobs(db_session) == 1
    db_session.refresh(job)
    assert job.status == "queued"
    assert "stopped responding" in job.final_status_reason


def test_a_recently_beating_job_is_left_alone(db_session):
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
    job = _job(db_session, "running", fresh)

    assert requeue_stale_jobs(db_session) == 0
    db_session.refresh(job)
    assert job.status == "running"


def test_a_running_job_with_no_heartbeat_is_requeued(db_session):
    """Rows predating the heartbeat column must still be recoverable."""
    job = _job(db_session, "running", None)
    assert requeue_stale_jobs(db_session) == 1
    db_session.refresh(job)
    assert job.status == "queued"


def test_queued_and_finished_jobs_are_untouched(db_session):
    for status in ("queued", "succeeded", "failed"):
        job = _job(db_session, status, None)
        assert requeue_stale_jobs(db_session) == 0
        db_session.refresh(job)
        assert job.status == status


def test_a_job_that_finishes_mid_sweep_is_not_knocked_back(db_session):
    """Regression: requeue_stale_jobs mutated the ORM object, so the emitted
    UPDATE carried no status predicate. A job that completed between the SELECT
    and the commit was reset to 'queued' and the movie rendered twice."""
    stale = datetime.now(timezone.utc) - timedelta(hours=48)
    job = _job(db_session, "running", stale)

    # Simulate the worker committing success from another session before the
    # sweep writes, by flipping the row underneath the loaded object.
    db_session.execute(
        update(RenderJob).where(RenderJob.id == job.id).values(status="succeeded")
    )
    db_session.commit()
    db_session.expire_all()

    assert requeue_stale_jobs(db_session) == 0
    db_session.refresh(job)
    assert job.status == "succeeded"


def test_worker_recovers_a_job_that_becomes_stale_after_startup(db_session, engine, monkeypatch):
    """An idle worker must sweep again after a heartbeat expires, without restarting."""
    now = datetime.now(timezone.utc)
    abandoned = _job(db_session, "running", now)
    active = _job(db_session, "running", now)
    abandoned_id, active_id = abandoned.id, active.id

    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=engine, future=True))
    monkeypatch.setattr(worker, "configure_logging", lambda: None)
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda: None)
    # Keep the queue consumer idle so the recovered row remains observable;
    # startup recovery and subsequent sweeps still use real database sessions.
    monkeypatch.setattr(worker, "process_next_job", lambda db: None)
    monkeypatch.setattr(worker, "_shutdown", False)
    monkeypatch.setattr(worker, "STALE_SWEEP_INTERVAL_SECONDS", 4)
    elapsed = 0
    sleeps = 0
    monkeypatch.setattr(worker.time, "monotonic", lambda: elapsed)

    def idle_sleep(seconds):
        nonlocal elapsed, sleeps
        elapsed += seconds
        sleeps += 1
        if sleeps == 1:
            # Startup already saw both jobs as fresh. One heartbeat becomes
            # stale only after the worker has entered its idle loop.
            db_session.execute(
                update(RenderJob)
                .where(RenderJob.id == abandoned_id)
                .values(heartbeat_at=now - timedelta(hours=48))
            )
            db_session.commit()
        else:
            db_session.expire_all()
            if db_session.get(RenderJob, abandoned_id).status == "queued" or sleeps >= 8:
                worker._shutdown = True

    monkeypatch.setattr(worker.time, "sleep", idle_sleep)
    worker.main()

    db_session.expire_all()
    recovered = db_session.get(RenderJob, abandoned_id)
    assert sleeps > 1
    assert recovered.status == "queued"
    assert recovered.final_status_reason == "Requeued after the previous worker stopped responding."
    assert db_session.get(RenderJob, active_id).status == "running"


def test_heartbeat_is_refreshed_during_a_render(db_session):
    """The worker must prove liveness while rendering, or a long render is
    declared dead and picked up by a second worker concurrently."""
    from backend.app.models import Clip, Movie, MovieSlot, RenderJobAttempt
    from backend.app.services.render_worker import process_next_job

    user = User(discord_user_id=str(uuid.uuid4()), display_name="U")
    db_session.add(user)
    db_session.flush()
    movie = Movie(user_id=user.id, title="M")
    db_session.add(movie)
    db_session.flush()
    for index in range(2):
        clip = Clip(
            user_id=user.id, original_name=f"T-p{index}-G.mp4", storage_path="/x",
            team="T", player=f"p{index}", tier="G", extension="mp4",
        )
        db_session.add(clip)
        db_session.flush()
        db_session.add(MovieSlot(movie_id=movie.id, clip_id=clip.id, sort_index=index))
    job = RenderJob(movie_id=movie.id, status="queued")
    db_session.add(job)
    db_session.flush()
    attempt = RenderJobAttempt(render_job_id=job.id, attempt_number=1)
    db_session.add(attempt)
    db_session.flush()
    job.latest_attempt_id = attempt.id
    db_session.commit()

    beats = []

    def renderer(clips, output, options=None, on_progress=None):
        assert on_progress is not None, "the worker must supply a liveness callback"
        on_progress()
        db_session.expire_all()
        beats.append(db_session.get(RenderJob, job.id).heartbeat_at)
        return {"duration": 1.0}

    process_next_job(db_session, renderer=renderer)

    assert beats and beats[0] is not None

    # The ordinary first claim must also leave just its final diagnostics.
    from backend.app.models import RenderJobClipResult

    results = db_session.scalars(
        select(RenderJobClipResult).where(RenderJobClipResult.render_job_attempt_id == attempt.id)
    ).all()
    assert len(results) == 2
    assert all(result.status == "accepted" for result in results)


def test_requeued_render_after_committed_heartbeat_has_only_accepted_clip_results(db_session, engine):
    """A dead worker's committed diagnostics must not be duplicated on recovery."""
    from backend.app.models import Clip, MovieSlot, RenderJobAttempt, RenderJobClipResult
    from backend.app.services.render_worker import process_next_job

    user = User(discord_user_id=str(uuid.uuid4()), display_name="U")
    db_session.add(user)
    db_session.flush()
    movie = Movie(user_id=user.id, title="M")
    db_session.add(movie)
    db_session.flush()
    for index in range(2):
        clip = Clip(
            user_id=user.id, original_name=f"T-p{index}-G.mp4",
            storage_path=f"/clip{index}.mp4", team="T", player=f"p{index}",
            tier="G", extension="mp4",
        )
        db_session.add(clip)
        db_session.flush()
        db_session.add(MovieSlot(movie_id=movie.id, clip_id=clip.id, sort_index=index))
    job = RenderJob(movie_id=movie.id, status="queued")
    db_session.add(job)
    db_session.flush()
    attempt = RenderJobAttempt(render_job_id=job.id, attempt_number=1)
    db_session.add(attempt)
    db_session.flush()
    job.latest_attempt_id = attempt.id
    job_id, attempt_id = job.id, attempt.id
    db_session.commit()

    def interrupted_render(clips, output, options=None, on_progress=None):
        on_progress()  # Commits the pending clip results before the worker dies.
        raise KeyboardInterrupt("worker stopped after heartbeat")

    with pytest.raises(KeyboardInterrupt):
        process_next_job(db_session, renderer=interrupted_render)
    db_session.close()

    # A separate worker session sees the previous heartbeat's committed rows.
    with sessionmaker(bind=engine, future=True)() as recovery:
        pending = recovery.scalars(
            select(RenderJobClipResult).where(RenderJobClipResult.render_job_attempt_id == attempt_id)
        ).all()
        assert len(pending) == 2
        assert all(result.status == "pending" for result in pending)
        recovery.execute(
            update(RenderJob).where(RenderJob.id == job_id).values(
                heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=48)
            )
        )
        recovery.commit()

        assert requeue_stale_jobs(recovery) == 1
        assert recovery.get(RenderJob, job_id).status == "queued"
        process_next_job(
            recovery,
            renderer=lambda clips, output, options=None, on_progress=None: {"duration": 1.0},
        )

        recovery.expire_all()
        recovered = recovery.get(RenderJob, job_id)
        results = recovery.scalars(
            select(RenderJobClipResult).where(
                RenderJobClipResult.render_job_attempt_id == recovered.latest_attempt_id
            )
        ).all()
        assert recovered.latest_attempt_id == attempt_id
        assert recovered.status == "succeeded"
        assert recovered.accepted_clip_count == 2
        assert len(results) == 2
        assert all(result.status == "accepted" for result in results)
        assert not any(result.status == "pending" for result in results)
