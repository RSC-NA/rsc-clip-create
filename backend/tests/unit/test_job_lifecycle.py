import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

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
