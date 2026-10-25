"""Removing a sweep file after the commit is best-effort.

A failure there leaves an orphan for the maintenance audit; it must never fail
the request, as in the clip, video and movie delete handlers. These need no
ffmpeg: the placeholder sweep from ``make_sweep`` is enough, and the external
media probe is stubbed for the replacement upload.
"""

from pathlib import Path

from backend.app.models import BrandSweep
from backend.app.routers import sweeps as sweeps_router


REPLACEMENT_SWEEP = b"replacement alpha sweep"


def _refuse_unlink_of(monkeypatch, target: Path):
    real_unlink = Path.unlink

    def guarded_unlink(self, *args, **kwargs):
        if Path(self) == target:
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", guarded_unlink)


def test_delete_succeeds_when_the_file_cannot_be_removed(
    client, db_session, make_sweep, monkeypatch, caplog
):
    sweep = make_sweep()
    stored = Path(sweep.storage_path)
    _refuse_unlink_of(monkeypatch, stored)

    with caplog.at_level("WARNING"):
        response = client.delete(f"/api/v1/sweeps/{sweep.id}")

    assert response.status_code == 204, response.text
    assert client.get("/api/v1/sweeps").json() is None
    assert "could not remove" in caplog.text
    # The orphan stays for `maintenance audit --fix`.
    assert stored.is_file()


def test_replacement_succeeds_when_the_previous_file_cannot_be_removed(
    client, db_session, isolated_storage, make_sweep, monkeypatch, caplog
):
    previous = make_sweep()
    previous_path = Path(previous.storage_path)

    def probe(path):
        assert Path(path).read_bytes() == REPLACEMENT_SWEEP
        return {"has_video": True, "pix_fmt": "argb", "duration": 0.5}

    monkeypatch.setattr(sweeps_router, "probe_media", probe)
    _refuse_unlink_of(monkeypatch, previous_path)

    with caplog.at_level("WARNING"):
        response = client.post(
            "/api/v1/sweeps",
            files=[("file", ("second.mov", REPLACEMENT_SWEEP, "video/quicktime"))],
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == previous.id
    assert body["originalName"] == "second.mov"
    new_path = Path(body["storagePath"])
    assert new_path != previous_path
    assert new_path.parent == isolated_storage / "sweeps"
    assert new_path.read_bytes() == REPLACEMENT_SWEEP
    assert "could not remove" in caplog.text

    db_session.expire_all()
    assert db_session.query(BrandSweep).count() == 1
    current = client.get("/api/v1/sweeps").json()
    assert current["storagePath"] == str(new_path)
    served = client.get(f"/api/v1/sweeps/{previous.id}/file")
    assert served.status_code == 200
    assert served.content == REPLACEMENT_SWEEP
    # The orphan stays for `maintenance audit --fix`.
    assert previous_path.is_file()
