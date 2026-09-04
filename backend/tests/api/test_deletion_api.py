"""Deletion is where the foreign key graph bites.

models.py declares no ondelete anywhere except the one added for slot removal;
cascades exist only at the ORM level. Getting the unwind order wrong raises
IntegrityError on MySQL -- and would pass silently on SQLite without the
PRAGMA the conftest now sets.
"""

from pathlib import Path

from backend.app.models import (
    Clip,
    Movie,
    MovieSlot,
    RenderJob,
    RenderJobAttempt,
    RenderJobClipResult,
    Video,
)


def _rendered_movie(db_session, make_movie, make_clip, tmp_path):
    """A movie with a timeline, a finished job, clip results and an output."""
    clip = make_clip()
    movie = make_movie(clips=[clip])
    slot = db_session.query(MovieSlot).filter_by(movie_id=movie.id).one()

    job = RenderJob(movie_id=movie.id, status="succeeded")
    db_session.add(job)
    db_session.flush()
    attempt = RenderJobAttempt(render_job_id=job.id, attempt_number=1, status="succeeded")
    db_session.add(attempt)
    db_session.flush()
    db_session.add(
        RenderJobClipResult(
            render_job_attempt_id=attempt.id, movie_slot_id=slot.id, clip_id=clip.id,
            clip_order_index=0, submitted_clip_path=clip.storage_path, status="accepted",
        )
    )
    rendered = tmp_path / "render.mp4"
    rendered.write_bytes(b"rendered bytes")
    video = Video(
        movie_id=movie.id, source_attempt_id=attempt.id,
        storage_path=str(rendered), status="succeeded",
    )
    db_session.add(video)
    db_session.flush()
    movie.latest_video_id = video.id
    db_session.commit()
    return movie, clip, rendered


# ---------------------------------------------------------------------------
# Movies
# ---------------------------------------------------------------------------
def test_deleting_a_rendered_movie_unwinds_the_whole_graph(
    client, db_session, make_movie, make_clip, tmp_path
):
    movie, clip, rendered = _rendered_movie(db_session, make_movie, make_clip, tmp_path)

    assert client.delete(f"/api/v1/movies/{movie.id}").status_code == 204

    db_session.expire_all()
    assert db_session.query(Movie).count() == 0
    assert db_session.query(MovieSlot).count() == 0
    assert db_session.query(RenderJob).count() == 0
    assert db_session.query(RenderJobAttempt).count() == 0
    assert db_session.query(RenderJobClipResult).count() == 0
    assert db_session.query(Video).count() == 0
    assert not rendered.exists()
    # The clip belongs to the library, not the movie, and must survive.
    assert db_session.query(Clip).count() == 1


def test_deleting_a_movie_mid_render_is_refused(client, db_session, make_movie, make_clip):
    movie = make_movie(clips=[make_clip()])
    db_session.add(RenderJob(movie_id=movie.id, status="running"))
    db_session.commit()

    response = client.delete(f"/api/v1/movies/{movie.id}")
    assert response.status_code == 409
    assert "in progress" in response.json()["detail"]
    assert db_session.query(Movie).count() == 1


def test_another_users_movie_cannot_be_deleted(other_client, db_session, make_movie):
    movie = make_movie()
    assert other_client.delete(f"/api/v1/movies/{movie.id}").status_code == 404
    assert db_session.query(Movie).count() == 1


def test_deleting_a_movie_with_no_render_history(client, db_session, make_movie):
    movie = make_movie()
    assert client.delete(f"/api/v1/movies/{movie.id}").status_code == 204
    assert db_session.query(Movie).count() == 0


# ---------------------------------------------------------------------------
# Clips
# ---------------------------------------------------------------------------
def test_a_clip_on_a_timeline_cannot_be_deleted(client, db_session, make_movie, make_clip):
    clip = make_clip()
    make_movie("Week 4", clips=[clip])

    response = client.delete(f"/api/v1/clips/{clip.id}")
    assert response.status_code == 409
    assert "Week 4" in response.json()["detail"]
    assert db_session.query(Clip).count() == 1


def test_an_unused_clip_is_deleted_with_its_file(
    client, db_session, make_clip, isolated_storage
):
    stored = isolated_storage / "clips" / "Falcons-Ace-Gold.mp4"
    stored.write_bytes(b"clip bytes")
    clip = make_clip("Falcons-Ace-Gold.mp4", storage_path=str(stored))

    assert client.delete(f"/api/v1/clips/{clip.id}").status_code == 204
    assert db_session.query(Clip).count() == 0
    assert not stored.exists()


def test_deleting_a_clip_detaches_its_render_history(
    client, db_session, make_movie, make_clip, tmp_path
):
    """The clip results are diagnostics and must survive the clip itself,
    otherwise the foreign key blocks the delete outright."""
    movie, clip, _ = _rendered_movie(db_session, make_movie, make_clip, tmp_path)
    client.put(f"/api/v1/movies/{movie.id}/timeline", json={"slots": []})

    assert client.delete(f"/api/v1/clips/{clip.id}").status_code == 204

    db_session.expire_all()
    results = db_session.query(RenderJobClipResult).all()
    assert len(results) == 1
    assert results[0].clip_id is None


def test_another_users_clip_cannot_be_deleted(other_client, db_session, make_clip):
    clip = make_clip()
    assert other_client.delete(f"/api/v1/clips/{clip.id}").status_code == 404
    assert db_session.query(Clip).count() == 1


# ---------------------------------------------------------------------------
# Videos
# ---------------------------------------------------------------------------
def test_deleting_a_video_clears_the_movies_pointer(
    client, db_session, make_movie, make_clip, tmp_path
):
    movie, _, rendered = _rendered_movie(db_session, make_movie, make_clip, tmp_path)
    video_id = movie.latest_video_id

    assert client.delete(f"/api/v1/videos/{video_id}").status_code == 204

    db_session.expire_all()
    assert db_session.query(Video).count() == 0
    assert db_session.get(Movie, movie.id).latest_video_id is None
    assert not rendered.exists()


def test_another_users_video_cannot_be_deleted(
    other_client, db_session, make_movie, make_clip, tmp_path
):
    movie, _, _ = _rendered_movie(db_session, make_movie, make_clip, tmp_path)
    assert other_client.delete(f"/api/v1/videos/{movie.latest_video_id}").status_code == 404
    assert db_session.query(Video).count() == 1


def test_deleting_a_missing_file_still_removes_the_row(
    client, db_session, make_movie, make_clip, tmp_path
):
    """A row pointing at a file that is already gone must still be removable."""
    movie, _, rendered = _rendered_movie(db_session, make_movie, make_clip, tmp_path)
    Path(rendered).unlink()

    assert client.delete(f"/api/v1/videos/{movie.latest_video_id}").status_code == 204
    assert db_session.query(Video).count() == 0
