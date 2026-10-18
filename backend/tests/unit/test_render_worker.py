import pytest

from backend.app.models import Clip, Movie, MovieSlot, RenderJob, RenderJobAttempt, User, Video
from backend.app.services.render_worker import process_next_job
from backend.app.services.renderer import RenderError


def _movie_with_clips(db, clip_count=2, *, team="Falcons", tier="Gold"):
    user = User(discord_user_id="123", display_name="Admin")
    db.add(user)
    db.flush()

    movie = Movie(user_id=user.id, title="Launch Reel")
    db.add(movie)
    db.flush()

    for index in range(clip_count):
        clip = Clip(
            user_id=user.id,
            original_name=f"{team}-player{index}-{tier}.mp4",
            storage_path=f"storage/clips/{index}.mp4",
            team=team,
            player=f"player{index}",
            tier=tier,
            extension="mp4",
        )
        db.add(clip)
        db.flush()
        db.add(MovieSlot(movie_id=movie.id, clip_id=clip.id, sort_index=index, overlay_text=""))

    job = RenderJob(movie_id=movie.id, status="queued")
    db.add(job)
    db.flush()
    attempt = RenderJobAttempt(render_job_id=job.id, attempt_number=1, status="queued")
    db.add(attempt)
    db.flush()
    job.latest_attempt_id = attempt.id
    db.commit()
    return movie, job


def test_success_creates_video_and_accepts_every_clip(db_session):
    _movie_with_clips(db_session, 3)
    captured = {}

    def fake_render(clips, output, options=None, on_progress=None):
        captured["clips"] = clips
        captured["options"] = options
        return {"duration": 46.2, "frames": 1386}

    processed = process_next_job(db_session, renderer=fake_render)
    video = db_session.query(Video).one()

    assert processed.status == "succeeded"
    assert processed.accepted_clip_count == 3
    assert processed.failed_clip_count == 0
    assert video.status == "succeeded"
    assert video.duration_seconds == 46.2
    assert len(captured["clips"]) == 3
    # Clips are handed to the renderer in timeline order.
    assert [c.name.player for c in captured["clips"]] == ["player0", "player1", "player2"]


def test_render_error_marks_job_and_clips_failed(db_session):
    _movie_with_clips(db_session, 2)

    def failing(clips, output, options=None, on_progress=None):
        raise RenderError("ffmpeg exploded", "overlay")

    processed = process_next_job(db_session, renderer=failing)

    assert processed.status == "failed"
    assert processed.failed_clip_count == 2
    assert processed.has_clip_level_failures is True
    assert db_session.query(Video).count() == 0
    attempt = processed.attempts[0]
    assert attempt.failure_stage == "overlay"
    assert "ffmpeg exploded" in attempt.error_summary


def test_unverified_final_render_does_not_create_succeeded_video(db_session, monkeypatch, isolated_storage):
    """The real renderer must not leave an orphan MP4 when verification fails."""
    from backend.app.services import renderer as renderer_module
    from backend.app.services.renderer import Segment
    from backend.app.storage import output_path

    movie, job = _movie_with_clips(db_session)
    destination = output_path(movie.id, job.latest_attempt_id)
    monkeypatch.setattr(renderer_module, "load_sweep", lambda options: None)
    monkeypatch.setattr(
        renderer_module, "normalize_clip",
        lambda spec, destination, workdir, options: Segment(destination, 60),
    )

    def join(segments, boundaries, output, options, workdir, **kwargs):
        output.write_bytes(b"incomplete movie")

    monkeypatch.setattr(renderer_module, "join_segments", join)
    monkeypatch.setattr(
        renderer_module, "probe_media",
        lambda path: {"duration": 4.0, "has_video": True, "has_audio": True, "nb_frames": 119},
    )

    processed = process_next_job(db_session, renderer=renderer_module.render_movie)

    assert processed.status == "failed"
    assert processed.attempts[0].failure_stage == "verify"
    assert processed.attempts[0].status == "failed"
    assert db_session.get(Movie, movie.id).status == "failed"
    assert db_session.query(Video).count() == 0
    assert not destination.exists()
    assert list((isolated_storage / "renders").glob("*.mp4")) == []


def test_video_flush_failure_removes_rendered_output(db_session, monkeypatch, isolated_storage):
    """A render that succeeded but whose Video row cannot be inserted must not
    leave its finished MP4 in storage with nothing referencing it."""
    from backend.app.storage import output_path

    movie, job = _movie_with_clips(db_session)
    destination = output_path(movie.id, job.latest_attempt_id)
    real_flush = db_session.flush
    state = {"raised": False}

    def flaky_flush(*args, **kwargs):
        pending_videos = [obj for obj in db_session.new if isinstance(obj, Video)]
        if pending_videos and not state["raised"]:
            state["raised"] = True
            for obj in pending_videos:
                db_session.expunge(obj)
            raise RuntimeError("video insert failed")
        return real_flush(*args, **kwargs)

    def fake_render(clips, output, options=None, on_progress=None):
        output.write_bytes(b"finished movie")
        monkeypatch.setattr(db_session, "flush", flaky_flush)
        return {"duration": 4.0}

    processed = process_next_job(db_session, renderer=fake_render)
    monkeypatch.setattr(db_session, "flush", real_flush)

    assert state["raised"] is True
    assert processed.status == "failed"
    assert processed.attempts[0].failure_stage == "worker"
    assert db_session.query(Video).count() == 0
    assert not destination.exists()
    assert list((isolated_storage / "renders").glob("*.mp4")) == []


def test_unrecordable_outcome_removes_rendered_output(db_session, engine, monkeypatch, isolated_storage):
    """When the final outcome commit raises, the rollback discards the Video
    row, so the finished MP4 must be deleted rather than orphaned."""
    from sqlalchemy.orm import sessionmaker

    from backend.app.storage import output_path

    movie, job = _movie_with_clips(db_session, 2)
    job_id = job.id
    destination = output_path(movie.id, job.latest_attempt_id)
    state = {"rendered": False, "failed": False}

    def fake_render(clips, output, options=None, on_progress=None):
        output.write_bytes(b"finished movie")
        state["rendered"] = True
        return {"duration": 3.0}

    real_commit = db_session.commit

    def flaky_commit():
        if state["rendered"] and not state["failed"]:
            state["failed"] = True
            raise RuntimeError("database went away")
        return real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)

    process_next_job(db_session, renderer=fake_render)

    assert state["failed"] is True
    fresh = sessionmaker(bind=engine, future=True)()
    try:
        stored_job = fresh.get(RenderJob, job_id)
        assert stored_job.status == "failed"
        assert stored_job.final_status_reason == "The render outcome could not be recorded."
        assert fresh.query(Video).count() == 0
    finally:
        fresh.close()
    assert not destination.exists()
    assert list((isolated_storage / "renders").glob("*.mp4")) == []


def test_unexpected_error_before_output_still_fails_job(db_session, isolated_storage):
    """Cleanup must tolerate a destination that was never written."""
    from backend.app.storage import output_path

    movie, job = _movie_with_clips(db_session, 2)
    destination = output_path(movie.id, job.latest_attempt_id)

    def exploding(clips, output, options=None, on_progress=None):
        raise ValueError("boom before output")

    processed = process_next_job(db_session, renderer=exploding)

    assert processed.status == "failed"
    assert processed.attempts[0].failure_stage == "worker"
    assert db_session.get(Movie, movie.id).status == "failed"
    assert not destination.exists()
    assert list((isolated_storage / "renders").glob("*.mp4")) == []


def test_cleanup_error_is_logged_not_raised(db_session, monkeypatch):
    """A failure while deleting the output must not escape process_next_job."""
    from backend.app.services import render_worker

    _movie_with_clips(db_session, 2)

    class Undeletable:
        def unlink(self, missing_ok=False):
            raise PermissionError("read-only storage")

    def exploding(clips, output, options=None, on_progress=None):
        monkeypatch.setattr(render_worker, "output_path", lambda *a: Undeletable())
        raise ValueError("boom")

    processed = process_next_job(db_session, renderer=exploding)

    assert processed.status == "failed"
    assert processed.attempts[0].failure_stage == "worker"


def test_success_keeps_rendered_output(db_session, isolated_storage):
    from backend.app.storage import output_path

    movie, job = _movie_with_clips(db_session, 2)
    destination = output_path(movie.id, job.latest_attempt_id)

    def fake_render(clips, output, options=None, on_progress=None):
        output.write_bytes(b"finished movie")
        return {"duration": 4.0}

    processed = process_next_job(db_session, renderer=fake_render)

    assert processed.status == "succeeded"
    assert destination.exists()
    assert db_session.query(Video).one().storage_path == str(destination)


def test_clips_missing_metadata_are_rejected_not_rendered(db_session):
    """Metadata validation used to be gated on the old Veo test seam, so it was
    skipped exactly when the seam was in use."""
    movie, _ = _movie_with_clips(db_session, 2)
    clip = db_session.query(Clip).first()
    clip.team = None
    db_session.commit()

    called = False

    def fake_render(clips, output, options=None, on_progress=None):
        nonlocal called
        called = True
        return {"duration": 1.0}

    processed = process_next_job(db_session, renderer=fake_render)

    # One valid clip left, which is below min_clip_count.
    assert processed.status == "failed"
    assert called is False


def test_min_clip_count_failure_counts_rejected_inputs(db_session):
    """The input RenderError raised for too few valid clips must count the
    clip rejected for incomplete metadata as well as the remaining one."""
    _movie_with_clips(db_session, 2)
    clip = db_session.query(Clip).first()
    clip.team = None
    db_session.commit()

    processed = process_next_job(db_session, renderer=lambda *a, **k: {"duration": 1.0})

    assert processed.status == "failed"
    assert processed.failed_clip_count == 2
    assert processed.has_clip_level_failures is True


def test_render_error_failed_clip_count_includes_rejected_inputs(db_session):
    """A render that fails after an input clip was rejected must count that
    rejected clip in failed_clip_count, as the success path already does."""
    from backend.app.models import RenderJobClipResult

    _movie_with_clips(db_session, 3)
    clip = db_session.query(Clip).first()
    clip.team = None
    db_session.commit()

    def failing(clips, output, options=None, on_progress=None):
        raise RenderError("ffmpeg exploded", "overlay")

    processed = process_next_job(db_session, renderer=failing)

    results = (
        db_session.query(RenderJobClipResult)
        .filter(RenderJobClipResult.render_job_attempt_id == processed.latest_attempt_id)
        .all()
    )
    assert processed.status == "failed"
    assert len(results) == 3
    assert all(r.status == "rejected" for r in results)
    assert processed.failed_clip_count == 3
    assert processed.has_clip_level_failures is True


def test_unexpected_error_failed_clip_count_includes_rejected_inputs(db_session):
    """An unexpected (non-RenderError) worker failure after an input clip was
    rejected must still count that rejected clip, as the RenderError path does.
    Clips left pending are not counted."""
    from backend.app.models import RenderJobClipResult

    _movie_with_clips(db_session, 3)
    clip = db_session.query(Clip).first()
    clip.team = None
    db_session.commit()

    def exploding(clips, output, options=None, on_progress=None):
        raise ValueError("boom")

    processed = process_next_job(db_session, renderer=exploding)

    results = (
        db_session.query(RenderJobClipResult)
        .filter(RenderJobClipResult.render_job_attempt_id == processed.latest_attempt_id)
        .all()
    )
    assert processed.status == "failed"
    assert processed.attempts[0].failure_stage == "worker"
    assert sorted(r.status for r in results) == ["pending", "pending", "rejected"]
    assert processed.failed_clip_count == 1
    assert processed.has_clip_level_failures is True


def test_unexpected_error_does_not_count_pending_clips(db_session):
    """With only valid clips, an unexpected worker error leaves them pending
    and reports no clip-level failures."""
    from backend.app.config import get_settings
    from backend.app.models import RenderJobClipResult

    # The renderer must actually be reached for this test to mean anything.
    assert get_settings().min_clip_count <= 2

    _movie_with_clips(db_session, 2)
    called = False

    def exploding(clips, output, options=None, on_progress=None):
        nonlocal called
        called = True
        raise ValueError("boom")

    processed = process_next_job(db_session, renderer=exploding)

    results = (
        db_session.query(RenderJobClipResult)
        .filter(RenderJobClipResult.render_job_attempt_id == processed.latest_attempt_id)
        .all()
    )
    assert called is True
    assert processed.status == "failed"
    assert processed.attempts[0].failure_stage == "worker"
    assert [r.status for r in results] == ["pending", "pending"]
    assert processed.failed_clip_count == 0
    assert processed.has_clip_level_failures is False


def test_unrecordable_outcome_fails_job_movie_and_attempt(db_session, engine, monkeypatch):
    """If the final outcome commit raises, the rollback discards the in-memory
    movie and attempt updates. _force_fail must still mark all three rows
    failed, or the movie shows as queued forever."""
    from sqlalchemy.orm import sessionmaker

    movie, job = _movie_with_clips(db_session, 2)
    movie_id, job_id, attempt_id = movie.id, job.id, job.latest_attempt_id

    state = {"rendered": False, "failed": False}

    def fake_render(clips, output, options=None, on_progress=None):
        state["rendered"] = True
        return {"duration": 3.0}

    real_commit = db_session.commit

    def flaky_commit():
        # Fail only the first commit after rendering: the outcome commit.
        if state["rendered"] and not state["failed"]:
            state["failed"] = True
            raise RuntimeError("database went away")
        return real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)

    process_next_job(db_session, renderer=fake_render)

    assert state["failed"] is True
    fresh = sessionmaker(bind=engine, future=True)()
    try:
        stored_job = fresh.get(RenderJob, job_id)
        stored_movie = fresh.get(Movie, movie_id)
        stored_attempt = fresh.get(RenderJobAttempt, attempt_id)
        assert stored_job.status == "failed"
        assert stored_job.final_status_reason == "The render outcome could not be recorded."
        assert stored_movie.status == "failed"
        assert stored_attempt.status == "failed"
        assert stored_attempt.failure_stage == "worker"
        assert stored_attempt.completed_at is not None
        assert fresh.query(Video).count() == 0
    finally:
        fresh.close()


def test_no_queued_job_returns_none(db_session):
    assert process_next_job(db_session, renderer=lambda *a, **k: {}) is None
