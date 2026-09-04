import uuid
from datetime import datetime, timedelta, timezone

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
