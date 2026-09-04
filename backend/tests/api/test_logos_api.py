"""The brand logo: one per user, centred in every transition sting.

This was a per-team library matched against the team token in a clip's
filename; clip labels are text-only now.
"""

import io

from PIL import Image

from backend.app.models import Logo


def _png(size=(64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(client, name="logo.png", content=None, content_type="image/png"):
    return client.post(
        "/api/v1/logos",
        files=[("file", (name, content if content is not None else _png(), content_type))],
    )


def test_no_logo_reads_as_null(client):
    assert client.get("/api/v1/logos").json() is None


def test_upload_then_read_back(client):
    assert _upload(client).status_code == 200
    body = client.get("/api/v1/logos").json()
    assert body is not None
    assert body["originalName"] == "logo.png"


def test_the_logo_is_scoped_to_the_caller(client, make_logo, other_user):
    make_logo(owner=other_user)
    assert client.get("/api/v1/logos").json() is None


def test_reuploading_replaces_rather_than_accumulating(client, db_session):
    _upload(client, "first.png")
    _upload(client, "second.png")

    assert db_session.query(Logo).count() == 1
    assert client.get("/api/v1/logos").json()["originalName"] == "second.png"


def test_replacing_removes_the_previous_file(client, db_session):
    from pathlib import Path

    _upload(client, "first.png")
    first_path = Path(db_session.query(Logo).one().storage_path)
    assert first_path.exists()

    _upload(client, "second.png")

    db_session.expire_all()
    second_path = Path(db_session.query(Logo).one().storage_path)
    assert second_path.exists()
    assert not first_path.exists(), "the replaced file was left orphaned on disk"


def test_a_non_image_is_rejected_and_not_stored(client, db_session, isolated_storage):
    response = _upload(client, content=b"definitely not a png")
    assert response.status_code == 400
    assert db_session.query(Logo).count() == 0
    assert list((isolated_storage / "logos").iterdir()) == []


def test_an_oversize_logo_is_rejected(client, db_session, monkeypatch):
    from backend.app.routers import logos as logos_router

    monkeypatch.setattr(logos_router.settings, "max_logo_size_bytes", 100)
    assert _upload(client).status_code == 400
    assert db_session.query(Logo).count() == 0


def test_an_oversize_dimension_is_rejected(client, db_session, monkeypatch):
    from backend.app.routers import logos as logos_router

    monkeypatch.setattr(logos_router.settings, "max_logo_dimension", 32)
    assert _upload(client, content=_png((256, 256))).status_code == 400
    assert db_session.query(Logo).count() == 0


def test_the_file_is_served_and_scoped(client, other_client, make_logo):
    logo = make_logo()
    assert client.get(f"/api/v1/logos/{logo.id}/file").status_code == 200
    assert other_client.get(f"/api/v1/logos/{logo.id}/file").status_code == 404


def test_delete_removes_the_row_and_the_file(client, db_session, make_logo):
    from pathlib import Path

    logo = make_logo()
    path = Path(logo.storage_path)
    assert path.exists()

    assert client.delete(f"/api/v1/logos/{logo.id}").status_code == 204
    assert db_session.query(Logo).count() == 0
    assert not path.exists()


def test_another_users_logo_cannot_be_deleted(other_client, db_session, make_logo):
    logo = make_logo()
    assert other_client.delete(f"/api/v1/logos/{logo.id}").status_code == 404
    assert db_session.query(Logo).count() == 1


def test_resolution_returns_the_path_for_the_sting(db_session, make_logo, user):
    from backend.app.routers.logos import resolve_brand_logo

    logo = make_logo()
    assert resolve_brand_logo(db_session, user.id) == __import__("pathlib").Path(
        logo.storage_path
    )


def test_resolution_is_none_without_a_logo(db_session, user):
    from backend.app.routers.logos import resolve_brand_logo

    assert resolve_brand_logo(db_session, user.id) is None


def test_a_row_pointing_at_a_missing_file_resolves_to_none(db_session, make_logo, user):
    """A broken logo must never fail a render; the sting just renders plain."""
    from pathlib import Path

    from backend.app.routers.logos import resolve_brand_logo

    logo = make_logo()
    Path(logo.storage_path).unlink()
    assert resolve_brand_logo(db_session, user.id) is None
