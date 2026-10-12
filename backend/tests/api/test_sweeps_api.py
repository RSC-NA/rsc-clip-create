"""The brand sweep: one per user, played over every cut between clips.

An account without one is not an account without transitions -- it renders
with the sweep bundled in the image. That distinction is what most of these
tests are about.
"""

from pathlib import Path

import pytest

from backend.app.models import BrandSweep
from backend.tests.conftest import requires_ffmpeg


pytestmark = requires_ffmpeg


@pytest.fixture
def video_bytes(make_media):
    """A small real video. The upload decodes what it is given, so the bundled
    90MB sweep would be a slow and pointless stand-in here."""
    return make_media("sweep.mov", seconds=0.5).read_bytes()


def _upload(client, video_bytes, name="sweep.mov", content_type="video/quicktime"):
    return client.post(
        "/api/v1/sweeps",
        files=[("file", (name, video_bytes, content_type))],
    )


def test_no_sweep_reads_as_null(client):
    """Null means "using the bundled one", not "no transition"."""
    assert client.get("/api/v1/sweeps").json() is None


def test_upload_then_read_back(client, video_bytes):
    assert _upload(client, video_bytes).status_code == 200
    body = client.get("/api/v1/sweeps").json()
    assert body["id"]
    assert body["originalName"] == "sweep.mov"
    assert body["durationSeconds"] == pytest.approx(0.5, abs=0.1)


def test_the_sweep_is_scoped_to_the_caller(client, make_sweep, other_user):
    make_sweep(owner=other_user)
    assert client.get("/api/v1/sweeps").json() is None


def test_uploading_again_replaces_rather_than_accumulates(client, db_session, video_bytes):
    _upload(client, video_bytes, name="first.mov")
    _upload(client, video_bytes, name="second.mov")
    assert db_session.query(BrandSweep).count() == 1
    assert client.get("/api/v1/sweeps").json()["originalName"] == "second.mov"


def test_replacing_removes_the_previous_file(client, db_session, video_bytes):
    _upload(client, video_bytes, name="first.mov")
    first_path = Path(db_session.query(BrandSweep).one().storage_path)
    assert first_path.is_file()

    db_session.expire_all()
    _upload(client, video_bytes, name="second.mov")
    second_path = Path(db_session.query(BrandSweep).one().storage_path)

    assert second_path.is_file()
    assert not first_path.is_file(), "the replaced sweep is still on disk"


def test_an_unsupported_extension_is_rejected(client, db_session, isolated_storage, video_bytes):
    response = _upload(client, video_bytes, name="sweep.gif", content_type="image/gif")
    assert response.status_code == 400
    assert "Unsupported sweep format" in response.json()["detail"]
    assert db_session.query(BrandSweep).count() == 0
    assert list((isolated_storage / "sweeps").iterdir()) == []


def test_a_file_that_is_not_video_is_rejected(client, db_session, isolated_storage):
    """The extension is not evidence. Without decoding it, an unusable sweep
    would only surface in the worker, one failed render at a time."""
    response = _upload(client, b"not really a video", name="sweep.mov")
    assert response.status_code == 400
    assert "not a readable video" in response.json()["detail"]
    assert db_session.query(BrandSweep).count() == 0
    assert list((isolated_storage / "sweeps").iterdir()) == []


def test_an_oversize_sweep_is_rejected(client, db_session, monkeypatch, video_bytes):
    from backend.app.routers import sweeps as sweeps_router

    monkeypatch.setattr(sweeps_router.settings, "max_brand_sweep_bytes", 100)
    assert _upload(client, video_bytes).status_code == 400
    assert db_session.query(BrandSweep).count() == 0


def test_the_file_is_served_and_scoped(client, other_client, make_sweep):
    sweep = make_sweep()
    assert client.get(f"/api/v1/sweeps/{sweep.id}/file").status_code == 200
    assert other_client.get(f"/api/v1/sweeps/{sweep.id}/file").status_code == 404


def test_delete_removes_the_row_and_the_file(client, db_session, make_sweep):
    sweep = make_sweep()
    path = Path(sweep.storage_path)
    assert path.is_file()

    assert client.delete(f"/api/v1/sweeps/{sweep.id}").status_code == 204
    assert db_session.query(BrandSweep).count() == 0
    assert not path.is_file()


def test_another_users_sweep_cannot_be_deleted(other_client, db_session, make_sweep):
    sweep = make_sweep()
    assert other_client.delete(f"/api/v1/sweeps/{sweep.id}").status_code == 404
    assert db_session.query(BrandSweep).count() == 1


# ---------------------------------------------------------------------------
# What the renderer is handed
# ---------------------------------------------------------------------------
def test_resolution_returns_the_uploaded_path(db_session, make_sweep, user):
    from backend.app.routers.sweeps import resolve_brand_sweep

    sweep = make_sweep()
    assert resolve_brand_sweep(db_session, user.id) == Path(sweep.storage_path)


def test_resolution_is_none_without_an_uploaded_sweep(db_session, user):
    """None is how the renderer is told to use the bundled sweep."""
    from backend.app.routers.sweeps import resolve_brand_sweep

    assert resolve_brand_sweep(db_session, user.id) is None


def test_a_row_pointing_at_a_missing_file_falls_back(db_session, make_sweep, user):
    """A broken sweep must never fail a render; the bundled one takes over."""
    from backend.app.routers.sweeps import resolve_brand_sweep

    sweep = make_sweep()
    Path(sweep.storage_path).unlink()
    assert resolve_brand_sweep(db_session, user.id) is None
