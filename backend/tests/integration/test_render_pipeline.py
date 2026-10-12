"""Real ffmpeg renders against generated clips.

These prove the placement arithmetic against the actual filter graph rather
than against itself. Skipped when ffmpeg is unavailable so the default suite
stays fast and portable.
"""

import subprocess

import pytest
from PIL import Image

from backend.app.filename_parser import ParsedClipName
from backend.app.services.brand import BUNDLED_SWEEP, COVER_THRESHOLD
from backend.app.services.renderer import (
    ClipSpec,
    RenderError,
    RenderOptions,
    load_sweep,
    render_movie,
)
from backend.app.storage import probe_media
from backend.tests.conftest import requires_ffmpeg


pytestmark = [pytest.mark.integration, requires_ffmpeg]

FPS = 30
SAMPLES_PER_FRAME = 48000 // FPS


def _options(**kwargs):
    return RenderOptions(**{**dict(movie_id="itest", fps=FPS), **kwargs})


def _specs(paths):
    return [
        ClipSpec(source=path, name=ParsedClipName("Falcons", f"P{index}", "Gold", "mp4"))
        for index, path in enumerate(paths)
    ]


def _clips(make_media, count, seconds=2):
    """Two seconds each: long enough on both sides to carry the sweep."""
    return [make_media(f"Falcons-P{i}-Gold.mp4", seconds=seconds) for i in range(count)]


# ---------------------------------------------------------------------------
# The sweep itself
# ---------------------------------------------------------------------------
def test_the_bundled_sweep_measures_as_expected():
    """The numbers the placement tests are worked against. If a replacement
    file is dropped in, this is what tells you the plan moved."""
    sweep = load_sweep(_options())
    assert sweep is not None
    assert sweep.path == BUNDLED_SWEEP
    assert sweep.frames == 44
    # Fully opaque from frame 10 to 32, so the cut belongs on 21.
    assert sweep.cover == 21
    assert sweep.lead_frames + sweep.tail_frames == sweep.frames


def test_disjoint_alpha_sweep_places_the_cut_under_an_opaque_frame(make_media, tmp_path):
    """Decode actual alpha, then place that same sweep over a rendered hard cut."""
    alpha = [255, 0, 0, 0, 255]
    for index, value in enumerate(alpha):
        Image.new("RGBA", (32, 32), (255, 0, 0, value)).save(
            tmp_path / f"sweep-{index:02d}.png"
        )
    path = tmp_path / "disjoint.mov"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(FPS), "-i", str(tmp_path / "sweep-%02d.png"),
            "-c:v", "qtrle", "-pix_fmt", "argb", str(path),
        ],
        check=True, capture_output=True,
    )
    options = _options(sweep_path=path, width=320, height=240)
    sweep = load_sweep(options)
    assert sweep is not None
    assert sweep.frames == len(alpha)
    assert alpha[sweep.cover] >= COVER_THRESHOLD

    clips = _clips(make_media, 2, seconds=1)
    media = render_movie(_specs(clips), tmp_path / "out.mp4", options)
    assert not media["transitions"][0].get("hard_cut")
    cut = media["transitions"][0]["start"] + media["sweep"]["cover"]
    assert cut == FPS  # The sweep's opaque cover is aligned with the clip join.
    assert alpha[cut - media["transitions"][0]["start"]] >= COVER_THRESHOLD


def test_a_sweep_that_is_not_video_degrades_instead_of_raising(tmp_path):
    """Every cut renders bare rather than the render failing."""
    broken = tmp_path / "broken.mov"
    broken.write_bytes(b"not a video")
    assert load_sweep(_options(sweep_path=broken)) is None


def test_a_missing_sweep_degrades_instead_of_raising(tmp_path):
    assert load_sweep(_options(sweep_path=tmp_path / "nope.mov")) is None


def test_a_sweep_with_no_alpha_still_gets_a_cut_frame(make_media, tmp_path):
    """testsrc2 is fully opaque throughout, so it never "covers" by the alpha
    test. The fallback is its midpoint, not a failure."""
    opaque = make_media("opaque.mp4", seconds=1)
    sweep = load_sweep(_options(sweep_path=opaque))
    assert sweep is not None
    assert sweep.cover == sweep.frames // 2


# ---------------------------------------------------------------------------
# Length and streams
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("count", [2, 3, 5])
def test_a_transition_adds_no_time_to_the_movie(make_media, tmp_path, count):
    """The sweep plays over the join rather than between the clips, so the
    finished movie is exactly as long as its clips."""
    clips = _clips(make_media, count)
    output = tmp_path / "out.mp4"

    media = render_movie(_specs(clips), output, _options())

    assert media["frames"] == 60 * count
    assert media["duration"] == pytest.approx(media["frames"] / FPS, abs=1 / FPS)
    assert all(not t.get("hard_cut") for t in media["transitions"])


def test_audio_and_video_stay_in_lockstep(make_media, tmp_path):
    clips = _clips(make_media, 3)
    output = tmp_path / "out.mp4"

    render_movie(_specs(clips), output, _options())

    probed = probe_media(output)
    assert probed["has_video"] and probed["has_audio"]
    assert probed["width"] == 1920 and probed["height"] == 1080


def test_a_clip_without_audio_still_renders(make_media, tmp_path):
    """normalize_clip always opens an anullsrc so pass 2 can map audio blindly."""
    clips = [
        make_media("Falcons-A-Gold.mp4", seconds=2, audio=False),
        make_media("Falcons-B-Gold.mp4", seconds=2, audio=True),
    ]
    output = tmp_path / "out.mp4"

    media = render_movie(_specs(clips), output, _options())

    assert probe_media(output)["has_audio"]
    assert media["duration"] > 0


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------
def test_each_sweep_is_centred_on_its_cut(make_media, tmp_path):
    clips = _clips(make_media, 3)
    media = render_movie(_specs(clips), tmp_path / "out.mp4", _options())

    cover = media["sweep"]["cover"]
    assert [t["start"] + cover for t in media["transitions"]] == [60, 120]


def test_clips_shorter_than_the_sweep_cut_bare(make_media, tmp_path):
    """A 15-frame clip cannot carry a 44-frame sweep on either side of it."""
    clips = [make_media(f"Falcons-P{i}-Gold.mp4", seconds=0.5) for i in range(3)]
    media = render_movie(_specs(clips), tmp_path / "out.mp4", _options())

    assert all(t.get("hard_cut") for t in media["transitions"])
    assert media["frames"] == 45


def test_a_missing_sweep_renders_the_whole_movie_bare(make_media, tmp_path):
    clips = _clips(make_media, 3)
    media = render_movie(
        _specs(clips), tmp_path / "out.mp4", _options(sweep_path=tmp_path / "gone.mov")
    )

    assert media["sweep"] is None
    assert all(t.get("hard_cut") for t in media["transitions"])
    assert media["frames"] == 180


def test_the_placement_is_reproducible(make_media, tmp_path):
    clips = _clips(make_media, 3)

    first = render_movie(_specs(clips), tmp_path / "a.mp4", _options())
    second = render_movie(_specs(clips), tmp_path / "b.mp4", _options())

    assert first["transitions"] == second["transitions"]


def test_grouping_does_not_move_a_single_sweep(make_media, tmp_path, monkeypatch):
    """Chunked rendering places a boundary against its group and then against
    the group outputs. Neither the decision nor the finished length may change
    with the chunk size."""
    from backend.app.services import renderer as renderer_module

    clips = _clips(make_media, 5)

    monkeypatch.setattr(renderer_module.settings, "render_chunk_size", 8)
    whole = render_movie(_specs(clips), tmp_path / "whole.mp4", _options())
    monkeypatch.setattr(renderer_module.settings, "render_chunk_size", 2)
    grouped = render_movie(_specs(clips), tmp_path / "grouped.mp4", _options())

    assert whole["frames"] == grouped["frames"]
    assert whole["transitions"] == grouped["transitions"]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------
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
    clips = _clips(make_media, 3, seconds=1)

    with pytest.raises(RenderError) as caught:
        render_movie(_specs(clips), tmp_path / "out.mp4", _options())
    assert caught.value.stage == "input"
    assert "at most" in str(caught.value)


def test_the_heartbeat_ticks_through_the_join_not_just_the_normalize(
    make_media, tmp_path, monkeypatch
):
    """Regression: on_progress fired once per normalized clip and once per
    encoded sting. Overlaying the sweep removed the sting encodes, and with
    them every ping during the join -- 78s of a measured 104s six-clip render
    passed with the heartbeat untouched, which requeue_stale_jobs reads as a
    dead worker."""
    from backend.app.services import renderer as renderer_module

    monkeypatch.setattr(renderer_module.settings, "render_chunk_size", 2)
    clips = _clips(make_media, 5, seconds=1)

    beats = []
    render_movie(
        _specs(clips), tmp_path / "out.mp4", _options(), on_progress=lambda: beats.append(1)
    )

    # One per clip, plus one per group join and one for the final join.
    assert len(beats) > len(clips), (
        f"{len(beats)} heartbeats for {len(clips)} clips: the join phase is silent"
    )
