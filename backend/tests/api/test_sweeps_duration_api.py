"""Sweep duration validation through HTTP, without requiring ffmpeg."""

from pathlib import Path

from backend.app.models import BrandSweep
from backend.app.routers import sweeps as sweeps_router


SHORT_SWEEP = b"short alpha sweep"
LONG_SWEEP = b"32 second alpha sweep"


def _upload(client, content, name):
    return client.post(
        "/api/v1/sweeps",
        files=[("file", (name, content, "video/quicktime"))],
    )


def _stub_alpha_probe(monkeypatch):
    # Only the external media probe is stubbed. Uploads, storage, database and
    # file-serving all go through the real application.
    def probe(path):
        content = Path(path).read_bytes()
        assert content in (SHORT_SWEEP, LONG_SWEEP)
        return {
            "has_video": True,
            "pix_fmt": "argb",
            "duration": 0.5 if content == SHORT_SWEEP else 32.0,
        }

    monkeypatch.setattr(sweeps_router, "probe_media", probe)
    monkeypatch.setattr(sweeps_router.settings, "max_clip_duration_seconds", 31)


def test_over_limit_alpha_sweep_is_rejected_without_creating_file_or_row(
    client, db_session, isolated_storage, monkeypatch
):
    _stub_alpha_probe(monkeypatch)

    rejected = _upload(client, LONG_SWEEP, "too-long.mov")

    assert rejected.status_code == 400, rejected.text
    assert "31 second limit" in rejected.json()["detail"]
    assert client.get("/api/v1/sweeps").json() is None
    assert db_session.query(BrandSweep).count() == 0
    assert list((isolated_storage / "sweeps").iterdir()) == []


def test_over_limit_replacement_preserves_short_sweep_and_serves_its_bytes(
    client, db_session, isolated_storage, monkeypatch
):
    _stub_alpha_probe(monkeypatch)

    first = _upload(client, SHORT_SWEEP, "first.mov")
    assert first.status_code == 200, first.text
    original = first.json()
    assert original["durationSeconds"] == 0.5
    prior_path = Path(original["storagePath"])
    assert prior_path.read_bytes() == SHORT_SWEEP

    rejected = _upload(client, LONG_SWEEP, "too-long.mov")
    assert rejected.status_code == 400, rejected.text
    db_session.expire_all()
    assert db_session.query(BrandSweep).count() == 1
    current = client.get("/api/v1/sweeps")
    assert current.status_code == 200
    assert current.json()["id"] == original["id"]
    assert current.json()["originalName"] == "first.mov"
    assert current.json()["storagePath"] == str(prior_path)
    assert current.json()["durationSeconds"] == 0.5
    assert prior_path.read_bytes() == SHORT_SWEEP
    served = client.get(f"/api/v1/sweeps/{original['id']}/file")
    assert served.status_code == 200
    assert served.content == SHORT_SWEEP
    assert list((isolated_storage / "sweeps").iterdir()) == [prior_path]
