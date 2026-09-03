from backend.app.models import Clip, Movie, MovieSlot, RenderJob, RenderJobAttempt, User, Video
from backend.app.services.render_worker import process_next_job


class FakeVeoClient:
    def submit_render(self, clip_paths, overlay_texts):
        return {
            "request_id": "req-test-123",
            "status": "submitted",
            "clip_count": len(clip_paths),
            "overlay_count": len([text for text in overlay_texts if text]),
        }


def test_process_next_job_marks_attempt_success_and_creates_video(db_session):
    user = User(discord_user_id="123", display_name="Admin")
    db_session.add(user)
    db_session.flush()

    movie = Movie(user_id=user.id, title="Launch Reel")
    db_session.add(movie)
    db_session.flush()

    clip_one = Clip(user_id=user.id, original_name="clip-a.mp4", storage_path="storage/clips/a.mp4")
    clip_two = Clip(user_id=user.id, original_name="clip-b.mp4", storage_path="storage/clips/b.mp4")
    db_session.add_all([clip_one, clip_two])
    db_session.flush()

    db_session.add_all(
        [
            MovieSlot(movie_id=movie.id, clip_id=clip_one.id, sort_index=0, overlay_text="Open strong"),
            MovieSlot(movie_id=movie.id, clip_id=clip_two.id, sort_index=1, overlay_text="Close with CTA"),
        ]
    )

    job = RenderJob(movie_id=movie.id, status="queued")
    db_session.add(job)
    db_session.flush()
    attempt = RenderJobAttempt(render_job_id=job.id, attempt_number=1, status="queued")
    db_session.add(attempt)
    db_session.flush()
    job.latest_attempt_id = attempt.id
    db_session.commit()

    processed = process_next_job(db_session, FakeVeoClient())
    created_video = db_session.query(Video).one()

    assert processed is not None
    assert processed.status == "succeeded"
    assert processed.accepted_clip_count == 2
    assert created_video.status == "succeeded"
