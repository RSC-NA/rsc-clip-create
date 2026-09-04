from backend.app.models import Video


def _video(db_session, movie, tmp_path, *, name="out.mp4"):
    path = tmp_path / name
    path.write_bytes(b"video bytes")
    video = Video(movie_id=movie.id, storage_path=str(path), status="succeeded")
    db_session.add(video)
    db_session.flush()
    movie.latest_video_id = video.id
    db_session.commit()
    return video, path


def test_location_and_file_are_served_to_the_owner(client, db_session, make_movie, tmp_path):
    video, _ = _video(db_session, make_movie(), tmp_path)

    location = client.get(f"/api/v1/videos/{video.id}/location")
    assert location.status_code == 200
    assert location.json()["videoUuid"] == video.id

    streamed = client.get(f"/api/v1/videos/{video.id}/file")
    assert streamed.status_code == 200
    assert streamed.content == b"video bytes"


def test_another_user_gets_404_on_both_routes(other_client, db_session, make_movie, tmp_path):
    video, _ = _video(db_session, make_movie(), tmp_path)
    assert other_client.get(f"/api/v1/videos/{video.id}/location").status_code == 404
    assert other_client.get(f"/api/v1/videos/{video.id}/file").status_code == 404


def test_a_missing_file_on_disk_is_404_not_500(client, db_session, make_movie, tmp_path):
    video, path = _video(db_session, make_movie(), tmp_path)
    path.unlink()
    assert client.get(f"/api/v1/videos/{video.id}/file").status_code == 404


def test_unknown_video_is_404(client):
    assert client.get("/api/v1/videos/nope/location").status_code == 404


def test_a_hostile_movie_title_does_not_break_the_download_header(
    client, db_session, make_movie, tmp_path
):
    """Starlette percent-encodes the filename, so CRLF cannot inject a header."""
    movie = make_movie('evil"\r\nX-Injected: yes')
    video, _ = _video(db_session, movie, tmp_path)

    response = client.get(f"/api/v1/videos/{video.id}/file")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "\n" not in disposition and "\r" not in disposition
    assert "x-injected" not in {k.lower() for k in response.headers}
