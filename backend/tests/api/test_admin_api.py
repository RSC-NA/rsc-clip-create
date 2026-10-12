import pytest

from backend.app.models import RenderJob, RenderJobAttempt, RenderJobClipResult, Video

ADMIN_ROUTES = [
    "/api/v1/admin/dashboard",
    "/api/v1/admin/users",
    "/api/v1/admin/videos",
    "/api/v1/admin/errors",
]


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_a_non_admin_is_forbidden(client, route):
    assert client.get(route).status_code == 403


@pytest.mark.parametrize("route", ADMIN_ROUTES)
def test_an_admin_is_allowed(admin_client, route):
    assert admin_client.get(route).status_code == 200


def test_dashboard_counts_reflect_the_data(admin_client, db_session, make_movie, make_clip):
    movie = make_movie(clips=[make_clip()])
    db_session.add(Video(movie_id=movie.id, storage_path="/tmp/x.mp4", status="succeeded"))
    db_session.commit()

    body = admin_client.get("/api/v1/admin/dashboard").json()
    assert body["totalMovies"] == 1
    assert body["totalVideos"] == 1
    assert body["totalUsers"] >= 1


def test_videos_report_their_source_attempt_not_a_later_retry(
    admin_client, db_session, make_movie
):
    movie = make_movie()
    job = RenderJob(movie_id=movie.id, status="queued")
    db_session.add(job)
    db_session.flush()
    succeeded = RenderJobAttempt(
        render_job_id=job.id, attempt_number=1, status="succeeded"
    )
    db_session.add(succeeded)
    db_session.flush()
    video = Video(
        movie_id=movie.id, source_attempt_id=succeeded.id,
        storage_path="/tmp/succeeded.mp4", status="succeeded",
    )
    db_session.add(video)
    db_session.flush()
    queued = RenderJobAttempt(render_job_id=job.id, attempt_number=2, status="queued")
    db_session.add(queued)
    db_session.flush()
    job.latest_attempt_id = queued.id
    db_session.commit()

    response = admin_client.get("/api/v1/admin/videos")
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}
    assert rows[video.id]["latestAttemptStatus"] == "succeeded"


def test_videos_without_source_attempt_report_null_even_with_movie_attempt(
    admin_client, db_session, make_movie
):
    movie = make_movie()
    job = RenderJob(movie_id=movie.id, status="queued")
    db_session.add(job)
    db_session.flush()
    queued = RenderJobAttempt(render_job_id=job.id, attempt_number=1, status="queued")
    db_session.add(queued)
    db_session.flush()
    job.latest_attempt_id = queued.id
    legacy_video = Video(
        movie_id=movie.id, source_attempt_id=None,
        storage_path="/tmp/legacy.mp4", status="succeeded",
    )
    db_session.add(legacy_video)
    db_session.commit()

    response = admin_client.get("/api/v1/admin/videos")
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}
    assert rows[legacy_video.id]["latestAttemptStatus"] is None


def test_errors_endpoint_survives_a_deleted_clip(
    admin_client, db_session, make_movie, make_clip
):
    """clipName must fall back rather than dereferencing a null relationship."""
    movie = make_movie(clips=[make_clip()])
    job = RenderJob(movie_id=movie.id, status="failed")
    db_session.add(job)
    db_session.flush()
    attempt = RenderJobAttempt(render_job_id=job.id, attempt_number=1, status="failed")
    db_session.add(attempt)
    db_session.flush()
    db_session.add(
        RenderJobClipResult(
            render_job_attempt_id=attempt.id, clip_id=None, clip_order_index=0,
            submitted_clip_path="", status="rejected",
        )
    )
    db_session.commit()

    body = admin_client.get("/api/v1/admin/errors").json()
    assert len(body) == 1
    assert body[0]["clipResults"][0]["clipName"] == "Deleted clip"


@pytest.mark.parametrize(
    ("statuses", "failed_count"),
    [
        (["pending", "pending"], 0),
        (["rejected", "pending"], 1),
    ],
)
def test_errors_count_only_rejected_clips(
    admin_client, db_session, make_movie, make_clip, statuses, failed_count
):
    clips = [make_clip(name=f"Team-Player-{index}.mp4") for index in range(2)]
    movie = make_movie(clips=clips)
    job = RenderJob(movie_id=movie.id, status="failed")
    db_session.add(job)
    db_session.flush()
    attempt = RenderJobAttempt(render_job_id=job.id, attempt_number=1, status="failed")
    db_session.add(attempt)
    db_session.flush()
    for index, (clip, status) in enumerate(zip(clips, statuses)):
        db_session.add(
            RenderJobClipResult(
                render_job_attempt_id=attempt.id,
                clip_id=clip.id,
                clip_order_index=index,
                submitted_clip_path=clip.storage_path,
                status=status,
            )
        )
    db_session.commit()

    response = admin_client.get("/api/v1/admin/errors")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "failed"
    assert body[0]["failedClipCount"] == failed_count
    assert {
        result["clipOrderIndex"]: result["status"] for result in body[0]["clipResults"]
    } == dict(enumerate(statuses))
