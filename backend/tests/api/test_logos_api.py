import io

from PIL import Image

from backend.app.models import Logo


def _png(size=(64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(client, team="Falcons", name="logo.png", content=None, content_type="image/png"):
    return client.post(
        "/api/v1/logos",
        data={"team": team},
        files=[("file", (name, content if content is not None else _png(), content_type))],
    )


def test_upload_and_list(client):
    assert _upload(client).status_code == 200
    body = client.get("/api/v1/logos").json()
    assert [logo["team"] for logo in body] == ["Falcons"]


def test_listing_is_scoped_to_the_caller(client, make_logo, other_user):
    make_logo("Ravens", owner=other_user)
    assert client.get("/api/v1/logos").json() == []


def test_reupload_replaces_rather_than_duplicating(client, db_session):
    _upload(client, "Falcons")
    _upload(client, "falcons")  # different case, same team

    assert db_session.query(Logo).count() == 1
    assert db_session.query(Logo).one().team == "falcons"


def test_a_blank_team_is_rejected(client):
    assert _upload(client, team="   ").status_code == 400


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


def test_the_file_is_served_and_scoped(client, other_client, make_logo):
    logo = make_logo("Falcons")
    assert client.get(f"/api/v1/logos/{logo.id}/file").status_code == 200
    assert other_client.get(f"/api/v1/logos/{logo.id}/file").status_code == 404


def test_delete_removes_the_row_and_the_file(client, db_session, make_logo):
    logo = make_logo("Falcons")
    from pathlib import Path

    path = Path(logo.storage_path)
    assert path.exists()

    assert client.delete(f"/api/v1/logos/{logo.id}").status_code == 204
    assert db_session.query(Logo).count() == 0
    assert not path.exists()


def test_another_users_logo_cannot_be_deleted(other_client, db_session, make_logo):
    logo = make_logo("Falcons")
    assert other_client.delete(f"/api/v1/logos/{logo.id}").status_code == 404
    assert db_session.query(Logo).count() == 1


def test_logo_resolution_matches_case_insensitively(db_session, make_logo, user):
    from backend.app.routers.logos import resolve_logo_path

    make_logo("Falcons")
    assert resolve_logo_path(db_session, user.id, "  falcons ") is not None
    assert resolve_logo_path(db_session, user.id, "Ravens") is None
    assert resolve_logo_path(db_session, user.id, None) is None


def test_a_row_pointing_at_a_missing_file_resolves_to_none(db_session, make_logo, user):
    """A broken logo must never fail a render."""
    from pathlib import Path

    from backend.app.routers.logos import resolve_logo_path

    logo = make_logo("Falcons")
    Path(logo.storage_path).unlink()
    assert resolve_logo_path(db_session, user.id, "Falcons") is None
