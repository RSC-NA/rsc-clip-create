"""HTTP-level tests for the movies router."""

from backend.app.models import Movie, MovieSlot, RenderJob, RenderJobAttempt, RenderJobClipResult


def _slot_payload(clips):
    return {
        "slots": [
            {"id": None, "sort_index": i, "clip_id": c.id, "overlay_text": ""}
            for i, c in enumerate(clips)
        ]
    }


def test_create_and_list_movie(client):
    created = client.post("/api/v1/movies", json={"title": "Week 4"})
    assert created.status_code == 200
    assert created.json()["title"] == "Week 4"

    listed = client.get("/api/v1/movies")
    assert listed.status_code == 200
    assert [m["title"] for m in listed.json()] == ["Week 4"]


def test_movie_detail_carries_no_transition_settings(client, make_movie):
    """Every cut is covered by the same brand sweep now, so there is nothing
    per-movie left to choose. The style picker and hold slider are gone."""
    body = client.get(f"/api/v1/movies/{make_movie().id}").json()
    assert "transitionStyle" not in body
    assert "transitionHoldSeconds" not in body
    assert "transitionStyles" not in body


def test_another_users_movie_is_404_not_403(other_client, make_movie):
    """Ownership is filtered into the query, so existence is not leaked."""
    movie = make_movie()
    assert other_client.get(f"/api/v1/movies/{movie.id}").status_code == 404


def test_missing_movie_is_404(client):
    assert client.get("/api/v1/movies/does-not-exist").status_code == 404


def test_empty_title_is_422_not_500(client):
    response = client.post("/api/v1/movies", json={"title": ""})
    assert response.status_code == 422


def test_overlong_title_is_rejected(client):
    response = client.post("/api/v1/movies", json={"title": "x" * 300})
    assert response.status_code == 422


def test_timeline_round_trip_preserves_order(client, make_movie, make_clip):
    movie = make_movie()
    clips = [make_clip(f"Falcons-P{i}-Gold.mp4") for i in range(3)]

    saved = client.put(f"/api/v1/movies/{movie.id}/timeline", json=_slot_payload(clips))
    assert saved.status_code == 200
    slots = saved.json()["slots"]
    assert [s["clipId"] for s in slots] == [c.id for c in clips]
    # The server mints its own ids; the client's None placeholders must not survive.
    assert all(s["id"] for s in slots)


def test_same_clip_in_two_slots_is_rejected(client, make_movie, make_clip):
    movie = make_movie()
    clip = make_clip()
    payload = _slot_payload([clip, clip])
    response = client.put(f"/api/v1/movies/{movie.id}/timeline", json=payload)
    assert response.status_code == 400
    assert "multiple timeline positions" in response.json()["detail"]


def test_another_users_clip_cannot_be_placed(client, make_movie, make_clip, other_user):
    movie = make_movie()
    foreign = make_clip("Ravens-Bo-Silver.mp4", owner=other_user)
    response = client.put(f"/api/v1/movies/{movie.id}/timeline", json=_slot_payload([foreign]))
    assert response.status_code == 400


def test_retired_transition_fields_are_ignored_not_rejected(client, make_movie, make_clip):
    """An editor tab left open across the deploy still saves its timeline."""
    movie = make_movie()
    clips = [make_clip(f"Falcons-P{i}-Gold.mp4") for i in range(2)]
    payload = _slot_payload(clips) | {"transition_style": "storm", "transition_hold_seconds": 1.2}

    response = client.put(f"/api/v1/movies/{movie.id}/timeline", json=payload)
    assert response.status_code == 200
    assert len(response.json()["slots"]) == 2


# ---------------------------------------------------------------------------
# Regression: removing a clip from a movie that has already been rendered
# ---------------------------------------------------------------------------
def _render_once(db_session, movie, clip):
    """Produce the RenderJobClipResult that used to block slot deletion."""
    slot = db_session.query(MovieSlot).filter_by(movie_id=movie.id).first()
    job = RenderJob(movie_id=movie.id, status="succeeded")
    db_session.add(job)
    db_session.flush()
    attempt = RenderJobAttempt(render_job_id=job.id, attempt_number=1, status="succeeded")
    db_session.add(attempt)
    db_session.flush()
    db_session.add(
        RenderJobClipResult(
            render_job_attempt_id=attempt.id,
            movie_slot_id=slot.id,
            clip_id=clip.id,
            clip_order_index=0,
            submitted_clip_path=clip.storage_path,
            status="accepted",
        )
    )
    db_session.commit()
    return slot


def test_removing_a_clip_from_a_rendered_movie_succeeds(client, db_session, make_movie, make_clip):
    """Regression: this returned 500 (FOREIGN KEY constraint failed).

    RenderJobClipResult.movie_slot_id referenced the slot with no ondelete and
    no relationship back, so the DELETE in update_timeline was rejected. The
    old SQLite fixture had foreign keys disabled and could not catch it.
    """
    clip = make_clip()
    movie = make_movie(clips=[clip])
    _render_once(db_session, movie, clip)

    response = client.put(f"/api/v1/movies/{movie.id}/timeline", json={"slots": []})

    assert response.status_code == 200, response.text
    assert response.json()["slots"] == []


def test_render_diagnostics_survive_the_slot_removal(client, db_session, make_movie, make_clip):
    """The clip result is detached, not deleted -- it is the render history."""
    clip = make_clip()
    movie = make_movie(clips=[clip])
    _render_once(db_session, movie, clip)

    client.put(f"/api/v1/movies/{movie.id}/timeline", json={"slots": []})

    db_session.expire_all()
    results = db_session.query(RenderJobClipResult).all()
    assert len(results) == 1
    assert results[0].movie_slot_id is None
    assert results[0].clip_id == clip.id


# ---------------------------------------------------------------------------
# Render queueing
# ---------------------------------------------------------------------------
def test_render_needs_the_minimum_clip_count(client, make_movie, make_clip):
    movie = make_movie(clips=[make_clip()])
    response = client.post(f"/api/v1/movies/{movie.id}/render")
    assert response.status_code == 400
    assert "at least" in response.json()["detail"]


def test_queueing_twice_is_409(client, make_movie, make_clip):
    clips = [make_clip(f"Falcons-P{i}-Gold.mp4") for i in range(2)]
    movie = make_movie(clips=clips)

    assert client.post(f"/api/v1/movies/{movie.id}/render").status_code == 200
    second = client.post(f"/api/v1/movies/{movie.id}/render")
    assert second.status_code == 409
    assert "already queued or running" in second.json()["detail"]


def test_retry_adds_an_attempt_rather_than_a_second_job(
    client, db_session, make_movie, make_clip
):
    clips = [make_clip(f"Falcons-P{i}-Gold.mp4") for i in range(2)]
    movie = make_movie(clips=clips)

    client.post(f"/api/v1/movies/{movie.id}/render")
    job = db_session.query(RenderJob).filter_by(movie_id=movie.id).one()
    job.status = "failed"
    db_session.commit()

    body = client.post(f"/api/v1/movies/{movie.id}/render").json()

    assert db_session.query(RenderJob).filter_by(movie_id=movie.id).count() == 1
    assert [a["attemptNumber"] for a in body["attempts"]] == [2, 1]


def test_render_requires_clip_metadata(client, db_session, make_movie, make_clip):
    clips = [make_clip(f"Falcons-P{i}-Gold.mp4") for i in range(2)]
    movie = make_movie(clips=clips)
    clips[0].team = None
    db_session.commit()

    response = client.post(f"/api/v1/movies/{movie.id}/render")
    assert response.status_code == 400
    assert "valid filename metadata" in response.json()["detail"]


def test_adding_a_brand_new_slot_succeeds(client, make_movie, make_clip):
    """Regression: update_timeline flushed a bare MovieSlot before assigning
    sort_index, which is NOT NULL -- so every slot added through the editor's
    Save Timeline returned 500."""
    movie = make_movie()
    clip = make_clip()

    response = client.put(
        f"/api/v1/movies/{movie.id}/timeline",
        json={"slots": [{"id": None, "sort_index": 0, "clip_id": clip.id, "overlay_text": "hi"}]},
    )

    assert response.status_code == 200, response.text
    slots = response.json()["slots"]
    assert len(slots) == 1
    assert slots[0]["overlayText"] == "hi"
    assert slots[0]["sortIndex"] == 0


def test_reordering_existing_slots_updates_in_place(client, make_movie, make_clip):
    movie = make_movie()
    first, second = make_clip("Falcons-A-Gold.mp4"), make_clip("Falcons-B-Gold.mp4")

    created = client.put(
        f"/api/v1/movies/{movie.id}/timeline",
        json={
            "slots": [
                {"id": None, "sort_index": 0, "clip_id": first.id, "overlay_text": ""},
                {"id": None, "sort_index": 1, "clip_id": second.id, "overlay_text": ""},
            ]
        },
    ).json()["slots"]

    swapped = client.put(
        f"/api/v1/movies/{movie.id}/timeline",
        json={
            "slots": [
                {"id": created[1]["id"], "sort_index": 0, "clip_id": second.id, "overlay_text": ""},
                {"id": created[0]["id"], "sort_index": 1, "clip_id": first.id, "overlay_text": ""},
            ]
        },
    )

    assert swapped.status_code == 200
    slots = swapped.json()["slots"]
    assert [s["clipId"] for s in slots] == [second.id, first.id]
    # Reordering reuses the rows rather than churning ids.
    assert {s["id"] for s in slots} == {created[0]["id"], created[1]["id"]}


def test_movie_detail_exposes_an_in_flight_job(client, make_movie, make_clip):
    """Regression: the edit page hardcoded initialJob=null and getJob() was
    imported by nothing, so reloading during a render showed "no render has
    been queued" and the job became unobservable."""
    clips = [make_clip(f"Falcons-P{i}-Gold.mp4") for i in range(2)]
    movie = make_movie(clips=clips)
    queued = client.post(f"/api/v1/movies/{movie.id}/render").json()

    detail = client.get(f"/api/v1/movies/{movie.id}").json()

    assert detail["latestJob"] is not None
    assert detail["latestJob"]["id"] == queued["id"]
    assert detail["latestJob"]["status"] == "queued"


def test_movie_detail_has_no_job_before_the_first_render(client, make_movie):
    assert client.get(f"/api/v1/movies/{make_movie().id}").json()["latestJob"] is None
