"""HTTP-level tests for clip upload and listing."""

import pytest

from backend.app.models import Clip
from backend.tests.conftest import requires_ffmpeg


def _upload(client, name, content=b"not a real video", content_type="video/mp4"):
    return client.post(
        "/api/v1/clips", files=[("files", (name, content, content_type))]
    )


def test_listing_is_scoped_to_the_caller(client, make_clip, other_user):
    make_clip("Falcons-Ace-Gold.mp4")
    make_clip("Ravens-Bo-Silver.mp4", owner=other_user)

    body = client.get("/api/v1/clips").json()
    assert [c["name"] for c in body] == ["Falcons-Ace-Gold.mp4"]


@pytest.mark.parametrize(
    "name",
    ["bad.mp4", "a-b.mp4", "a-b-c-d.mp4", "a-b-c.exe", "-b-c.mp4", "no-extension"],
)
def test_malformed_filenames_are_rejected(client, name):
    response = _upload(client, name)
    assert response.status_code == 400
    assert name in response.json()["detail"]


def test_a_filename_with_a_path_separator_is_rejected(client):
    """Regression: this parsed fine, and Path('<uuid>-sub/A-b-c.mp4').name
    collapsed to 'A-b-c.mp4' -- dropping the uuid prefix, so two users writing
    the same team-player-tier name overwrote each other's footage."""
    response = _upload(client, "sub/Falcons-Ace-Gold.mp4")
    assert response.status_code == 400
    assert "path separator" in response.json()["detail"]


def test_nothing_is_written_when_a_filename_is_rejected(client, isolated_storage, db_session):
    _upload(client, "sub/Falcons-Ace-Gold.mp4")
    assert list((isolated_storage / "clips").iterdir()) == []
    assert db_session.query(Clip).count() == 0


def test_an_unreadable_file_is_rejected_and_cleaned_up(client, isolated_storage, db_session):
    """The name is valid, so it reaches disk; ffprobe then rejects it."""
    response = _upload(client, "Falcons-Ace-Gold.mp4", b"still not a video")
    assert response.status_code == 400
    assert list((isolated_storage / "clips").iterdir()) == []
    assert db_session.query(Clip).count() == 0


def test_empty_upload_is_rejected(client):
    response = client.post("/api/v1/clips", files=[])
    assert response.status_code in (400, 422)


@requires_ffmpeg
def test_a_real_clip_uploads_and_parses_its_metadata(client, make_media, db_session):
    media = make_media("Falcons-Ace-Gold.mp4", seconds=1)
    response = client.post(
        "/api/v1/clips",
        files=[("files", ("Falcons-Ace-Gold.mp4", media.read_bytes(), "video/mp4"))],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert (body[0]["team"], body[0]["player"], body[0]["tier"]) == ("Falcons", "Ace", "Gold")
    assert body[0]["durationSeconds"] == pytest.approx(1.0, abs=0.3)
    assert db_session.query(Clip).count() == 1


@requires_ffmpeg
def test_the_uuid_prefix_survives_on_disk(client, make_media, isolated_storage):
    """Two uploads of the same name must not collide."""
    media = make_media("Falcons-Ace-Gold.mp4", seconds=1)
    payload = media.read_bytes()
    for _ in range(2):
        client.post(
            "/api/v1/clips",
            files=[("files", ("Falcons-Ace-Gold.mp4", payload, "video/mp4"))],
        )

    written = sorted(p.name for p in (isolated_storage / "clips").iterdir())
    assert len(written) == 2, written
    assert all(n.endswith("-Falcons-Ace-Gold.mp4") and len(n) > len("Falcons-Ace-Gold.mp4") for n in written)


@requires_ffmpeg
def test_a_clip_over_the_duration_cap_is_rejected(client, make_media, db_session, monkeypatch):
    from backend.app.routers import clips as clips_router

    monkeypatch.setattr(clips_router.settings, "max_clip_duration_seconds", 0.5)
    media = make_media("Falcons-Ace-Gold.mp4", seconds=2)

    response = client.post(
        "/api/v1/clips",
        files=[("files", ("Falcons-Ace-Gold.mp4", media.read_bytes(), "video/mp4"))],
    )
    assert response.status_code == 400
    assert "second limit" in response.json()["detail"]
    assert db_session.query(Clip).count() == 0


@requires_ffmpeg
def test_one_bad_file_leaves_no_partial_batch(client, make_media, isolated_storage, db_session):
    """A rejected file must not leave the already-written good ones behind."""
    good = make_media("Falcons-Ace-Gold.mp4", seconds=1).read_bytes()
    response = client.post(
        "/api/v1/clips",
        files=[
            ("files", ("Falcons-Ace-Gold.mp4", good, "video/mp4")),
            ("files", ("nope.mp4", b"garbage", "video/mp4")),
        ],
    )

    assert response.status_code == 400
    assert db_session.query(Clip).count() == 0
    assert list((isolated_storage / "clips").iterdir()) == []


@requires_ffmpeg
def test_uploading_with_a_movie_id_attaches_slots(client, make_media, make_movie, db_session):
    from backend.app.models import MovieSlot

    movie = make_movie()
    media = make_media("Falcons-Ace-Gold.mp4", seconds=1)

    response = client.post(
        f"/api/v1/clips?movie_id={movie.id}",
        files=[("files", ("Falcons-Ace-Gold.mp4", media.read_bytes(), "video/mp4"))],
    )

    assert response.status_code == 200
    assert db_session.query(MovieSlot).filter_by(movie_id=movie.id).count() == 1


def test_an_over_budget_request_is_rejected_before_anything_is_written(
    client, db_session, isolated_storage, monkeypatch
):
    """Regression: the whole multipart body is buffered by the Next proxy before
    the API sees it. With the proxy capped at 10MB and the API advertising a
    500MB per-clip limit, a routine upload was truncated mid-body and the
    upstream connection reset, with no usable error anywhere."""
    from backend.app.routers import clips as clips_router

    monkeypatch.setattr(clips_router.settings, "max_upload_bytes", 1000)

    response = client.post(
        "/api/v1/clips",
        files=[("files", ("Falcons-Ace-Gold.mp4", b"x" * 5000, "video/mp4"))],
    )

    assert response.status_code == 413
    assert "limit is" in response.json()["detail"]
    assert db_session.query(Clip).count() == 0
    assert list((isolated_storage / "clips").iterdir()) == []


def test_the_advertised_upload_budget_is_at_least_one_clip(client):
    """A per-clip limit above the per-request budget would be unusable."""
    body = client.get("/api/v1/config").json()
    assert body["maxUploadBytes"] >= body["maxClipSizeBytes"]
