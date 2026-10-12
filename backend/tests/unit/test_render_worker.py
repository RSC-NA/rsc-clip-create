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
    assert db_session.query(Video).count() == 0
    attempt = processed.attempts[0]
    assert attempt.failure_stage == "overlay"
    assert "ffmpeg exploded" in attempt.error_summary


def test_unverified_final_render_does_not_create_succeeded_video(db_session, monkeypatch):
    """The worker only records success if the actual renderer verifies its output."""
    from backend.app.services import renderer as renderer_module
    from backend.app.services.renderer import Segment

    movie, _ = _movie_with_clips(db_session)
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


def test_no_queued_job_returns_none(db_session):
    assert process_next_job(db_session, renderer=lambda *a, **k: {}) is None
