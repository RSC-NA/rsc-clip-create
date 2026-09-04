"""Unbounded user input used to reach sized columns and surface as a 500.

Each of these is a 422 or 400 now, with a message the UI can show.
"""

import io

import pytest
from PIL import Image

from backend.app.models import Clip


def test_an_out_of_range_sort_index_is_422(client, make_movie):
    movie = make_movie()
    response = client.put(
        f"/api/v1/movies/{movie.id}/timeline",
        json={"slots": [{"id": None, "sort_index": 10**12, "clip_id": None, "overlay_text": ""}]},
    )
    assert response.status_code == 422


def test_a_negative_sort_index_is_422(client, make_movie):
    movie = make_movie()
    response = client.put(
        f"/api/v1/movies/{movie.id}/timeline",
        json={"slots": [{"id": None, "sort_index": -1, "clip_id": None, "overlay_text": ""}]},
    )
    assert response.status_code == 422


def test_enormous_overlay_text_is_422(client, make_movie):
    movie = make_movie()
    response = client.put(
        f"/api/v1/movies/{movie.id}/timeline",
        json={
            "slots": [
                {"id": None, "sort_index": 0, "clip_id": None, "overlay_text": "x" * 100_000}
            ]
        },
    )
    assert response.status_code == 422


def test_an_absurd_number_of_slots_is_422(client, make_movie):
    movie = make_movie()
    slots = [
        {"id": None, "sort_index": i, "clip_id": None, "overlay_text": ""} for i in range(2000)
    ]
    assert client.put(f"/api/v1/movies/{movie.id}/timeline", json={"slots": slots}).status_code == 422


@pytest.mark.parametrize("segment", ["team", "player", "tier"])
def test_an_overlong_filename_segment_is_rejected(client, db_session, segment):
    parts = {"team": "Falcons", "player": "Ace", "tier": "Gold"}
    parts[segment] = "x" * 300
    name = f"{parts['team']}-{parts['player']}-{parts['tier']}.mp4"

    response = client.post("/api/v1/clips", files=[("files", (name, b"data", "video/mp4"))])
    assert response.status_code == 400
    assert db_session.query(Clip).count() == 0


def test_an_oversize_logo_dimension_is_rejected(client, db_session, monkeypatch):
    from backend.app.routers import logos as logos_router

    monkeypatch.setattr(logos_router.settings, "max_logo_dimension", 64)
    buffer = io.BytesIO()
    Image.new("RGBA", (256, 256), (255, 0, 0, 255)).save(buffer, format="PNG")

    response = client.post(
        "/api/v1/logos",
        data={"team": "Falcons"},
        files=[("file", ("logo.png", buffer.getvalue(), "image/png"))],
    )
    assert response.status_code == 400
    assert "maximum is" in response.json()["detail"]


def test_too_many_clips_is_refused_at_queue_time(client, db_session, make_movie, make_clip, monkeypatch):
    """Previously only the worker enforced this, so the user waited for a
    render that was never going to run."""
    from backend.app.routers import movies as movies_router

    monkeypatch.setattr(movies_router.settings, "render_max_graph_inputs", 2)
    clips = [make_clip(f"Falcons-P{i}-Gold.mp4") for i in range(3)]
    movie = make_movie(clips=clips)

    response = client.post(f"/api/v1/movies/{movie.id}/render")
    assert response.status_code == 400
    assert "at most 2" in response.json()["detail"]
