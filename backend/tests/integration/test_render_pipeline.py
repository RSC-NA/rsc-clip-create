"""Real ffmpeg renders against generated clips.

These prove the offset arithmetic against the actual filter graph rather than
against itself. Skipped when ffmpeg is unavailable so the default suite stays
fast and portable.
"""

import pytest

from backend.app.filename_parser import ParsedClipName
from backend.app.services.brand import TRANSITION_FAMILIES
from backend.app.services.renderer import (
    ClipSpec,
    RenderError,
    RenderOptions,
    render_movie,
)
from backend.app.storage import probe_media
from backend.tests.conftest import requires_ffmpeg


pytestmark = [pytest.mark.integration, requires_ffmpeg]

FPS = 30
SAMPLES_PER_FRAME = 48000 // FPS


def _options(**kwargs):
    defaults = dict(movie_id="itest", fps=FPS, hold_frames=6, blend_frames=3, family="sweep")
    return RenderOptions(**{**defaults, **kwargs})


def _specs(paths, logo_path=None):
    return [
        ClipSpec(
            source=path,
            name=ParsedClipName("Falcons", f"P{index}", "Gold", "mp4"),
            logo_path=logo_path,
        )
        for index, path in enumerate(paths)
    ]


@pytest.mark.parametrize("count", [2, 3, 5])
def test_duration_is_clips_plus_one_hold_per_boundary(make_media, tmp_path, count):
    clips = [make_media(f"Falcons-P{i}-Gold.mp4", seconds=1) for i in range(count)]
    options = _options()
    output = tmp_path / "out.mp4"

    media = render_movie(_specs(clips), output, options)

    expected_frames = media["frames"]
    assert media["duration"] == pytest.approx(expected_frames / FPS, abs=1 / FPS)
    # One hold per boundary, and no more.
    holds = (count - 1) * options.hold_frames
    assert expected_frames == sum(s for s in [30] * count) + holds


def test_audio_and_video_stay_in_lockstep(make_media, tmp_path):
    clips = [make_media(f"Falcons-P{i}-Gold.mp4", seconds=1) for i in range(3)]
    output = tmp_path / "out.mp4"

    render_movie(_specs(clips), output, _options())

    probed = probe_media(output)
    assert probed["has_video"] and probed["has_audio"]
    assert probed["width"] == 1920 and probed["height"] == 1080


def test_a_clip_without_audio_still_renders(make_media, tmp_path):
    """render_clip always opens an anullsrc so pass 2 can map audio blindly."""
    clips = [
        make_media("Falcons-A-Gold.mp4", seconds=1, audio=False),
        make_media("Falcons-B-Gold.mp4", seconds=1, audio=True),
    ]
    output = tmp_path / "out.mp4"

    media = render_movie(_specs(clips), output, _options())

    assert probe_media(output)["has_audio"]
    assert media["duration"] > 0


def test_zero_hold_produces_hard_cuts_and_no_added_time(make_media, tmp_path):
    clips = [make_media(f"Falcons-P{i}-Gold.mp4", seconds=1) for i in range(3)]
    output = tmp_path / "out.mp4"

    media = render_movie(_specs(clips), output, _options(hold_frames=0))

    assert all(t.get("hard_cut") for t in media["transitions"])
    assert media["frames"] == 90


@pytest.mark.parametrize("family", sorted(TRANSITION_FAMILIES))
def test_every_transition_family_renders(make_media, tmp_path, family):
    clips = [make_media(f"Falcons-P{i}-Gold.mp4", seconds=1) for i in range(2)]
    output = tmp_path / f"{family}.mp4"

    media = render_movie(_specs(clips), output, _options(family=family))

    assert output.stat().st_size > 0
    assert media["transitions"][0]["family"] == family


def test_the_transition_sequence_is_reproducible(make_media, tmp_path):
    clips = [make_media(f"Falcons-P{i}-Gold.mp4", seconds=1) for i in range(3)]

    first = render_movie(_specs(clips), tmp_path / "a.mp4", _options())
    second = render_movie(_specs(clips), tmp_path / "b.mp4", _options())

    assert first["transitions"] == second["transitions"]


def test_a_different_movie_gets_different_transitions(make_media, tmp_path):
    clips = [make_media(f"Falcons-P{i}-Gold.mp4", seconds=1) for i in range(3)]

    a = render_movie(_specs(clips), tmp_path / "a.mp4", _options(movie_id="one"))
    b = render_movie(_specs(clips), tmp_path / "b.mp4", _options(movie_id="two"))

    assert [t["in"] for t in a["transitions"]] != [t["in"] for t in b["transitions"]] or \
           [t["axis"] for t in a["transitions"]] != [t["axis"] for t in b["transitions"]]


def test_a_logo_is_applied_to_one_team_and_not_another(make_media, tmp_path, make_logo):
    from pathlib import Path

    logo = Path(make_logo("Falcons").storage_path)
    clips = [make_media(f"Falcons-P{i}-Gold.mp4", seconds=1) for i in range(2)]
    specs = [
        ClipSpec(clips[0], ParsedClipName("Falcons", "A", "Gold", "mp4"), logo_path=logo),
        ClipSpec(clips[1], ParsedClipName("Ravens", "B", "Silver", "mp4"), logo_path=None),
    ]
    output = tmp_path / "out.mp4"

    render_movie(specs, output, _options())
    assert output.stat().st_size > 0


def test_a_missing_source_fails_with_an_attributable_stage(tmp_path):
    specs = _specs([tmp_path / "does-not-exist.mp4"])
    with pytest.raises(RenderError) as caught:
        render_movie(specs, tmp_path / "out.mp4", _options())
    assert caught.value.stage == "input"


def test_a_zero_byte_clip_fails_at_probe(tmp_path):
    empty = tmp_path / "Falcons-A-Gold.mp4"
    empty.write_bytes(b"")
    with pytest.raises(RenderError) as caught:
        render_movie(_specs([empty]), tmp_path / "out.mp4", _options())
    assert caught.value.stage == "probe"


def test_too_many_clips_is_refused_before_ffmpeg_runs(make_media, tmp_path, monkeypatch):
    from backend.app.services import renderer as renderer_module

    monkeypatch.setattr(renderer_module.settings, "render_max_graph_inputs", 2)
    clips = [make_media(f"Falcons-P{i}-Gold.mp4", seconds=1) for i in range(3)]

    with pytest.raises(RenderError) as caught:
        render_movie(_specs(clips), tmp_path / "out.mp4", _options())
    assert caught.value.stage == "input"
    assert "at most" in str(caught.value)
