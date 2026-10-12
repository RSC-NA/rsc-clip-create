"""The transition arithmetic, tested without invoking ffmpeg.

Where the sweep starts is the easiest thing in the pipeline to get subtly
wrong -- a frame out and the cut happens while the picture is still visible --
so the placements are asserted directly against hand-worked numbers.
"""

from pathlib import Path

import pytest

from backend.app.services.renderer import (
    RenderError,
    RenderOptions,
    Segment,
    Sweep,
    build_chain_command,
    decide_boundaries,
    place_boundaries,
    plan_boundaries,
    total_frames,
)


def _segments(count, frames=450):
    return [Segment(Path(f"clip-{i}.mp4"), frames) for i in range(count)]


OPTIONS = RenderOptions(movie_id="demo", fps=30)

# The bundled sweep, measured: 44 frames, fully covering from 10 to 32, so the
# cut belongs on frame 21.
SWEEP = Sweep(path=Path("brand-sweep.mov"), frames=44, cover=21)


def _graph(command):
    return command[command.index("-filter_complex") + 1]


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------
def test_three_clip_placement_matches_the_worked_example():
    """3 x 450 frames. Cuts land on 450 and 900; a sweep that covers on its
    own frame 21 therefore starts 21 frames earlier, on 429 and 879."""
    segments = _segments(3)
    boundaries = plan_boundaries(segments, SWEEP)

    assert [b.start for b in boundaries] == [429, 879]
    assert total_frames(segments, boundaries) == 1350
    assert total_frames(segments, boundaries) / OPTIONS.fps == pytest.approx(45.0)


@pytest.mark.parametrize("count", [2, 3, 5, 12])
def test_a_transition_costs_the_movie_no_time(count):
    """The sweep is overlaid on the join, not spliced into it. This is the
    headline difference from the generated card it replaced, which added a
    hold to the running time at every boundary."""
    segments = _segments(count)
    boundaries = plan_boundaries(segments, SWEEP)
    assert total_frames(segments, boundaries) == 450 * count


def test_each_sweep_is_centred_on_its_own_cut():
    segments = _segments(6)
    boundaries = plan_boundaries(segments, SWEEP)
    for index, boundary in enumerate(boundaries):
        cut = 450 * (index + 1)
        assert boundary.start + SWEEP.cover == cut


def test_starts_are_strictly_increasing_and_inside_the_movie():
    segments = _segments(6)
    boundaries = plan_boundaries(segments, SWEEP)
    starts = [b.start for b in boundaries]
    assert starts == sorted(starts)
    assert starts[0] >= 0
    assert starts[-1] + SWEEP.frames <= total_frames(segments, boundaries)


def test_single_clip_has_no_boundaries():
    assert plan_boundaries(_segments(1), SWEEP) == []


def test_sweeps_never_overlap_each_other():
    """Two sweeps running at once over one short clip would read as a glitch."""
    segments = _segments(4, frames=60)
    boundaries = [b for b in plan_boundaries(segments, SWEEP) if not b.hard_cut]
    for earlier, later in zip(boundaries, boundaries[1:]):
        assert earlier.start + SWEEP.frames <= later.start


# --------------------------------------------------------------------------
# Degrading to a bare cut
# --------------------------------------------------------------------------
def test_a_clip_shorter_than_the_sweep_cuts_bare():
    """There is nowhere to put a 44-frame sweep around a 30-frame clip without
    running past the clip on the far side of it."""
    segments = [Segment(Path("a"), 450), Segment(Path("b"), 30), Segment(Path("c"), 450)]
    boundaries = plan_boundaries(segments, SWEEP)
    assert [b.hard_cut for b in boundaries] == [True, True]
    assert total_frames(segments, boundaries) == 930


def test_a_clip_exactly_as_long_as_the_sweep_still_carries_one():
    segments = [Segment(Path("a"), 44), Segment(Path("b"), 44)]
    boundaries = plan_boundaries(segments, SWEEP)
    assert not boundaries[0].hard_cut
    assert boundaries[0].start == 23


def test_no_sweep_at_all_renders_every_cut_bare():
    """A missing or unreadable sweep file must not fail the render."""
    segments = _segments(3)
    boundaries = plan_boundaries(segments, None)
    assert all(b.hard_cut for b in boundaries)
    assert total_frames(segments, boundaries) == 1350


# --------------------------------------------------------------------------
# The graph
# --------------------------------------------------------------------------
def test_graph_references_every_input_and_maps_the_final_labels():
    segments = _segments(3)
    boundaries = plan_boundaries(segments, SWEEP)
    command = build_chain_command(segments, boundaries, Path("out.mp4"), OPTIONS)
    graph = _graph(command)

    # 3 clips + the sweep, opened once per cut.
    assert command.count("-i") == 5
    for index in range(3):
        assert f"[v{index}]" in graph and f"[a{index}]" in graph
    # One concat for the whole run; every cut is hard underneath the sweep.
    assert graph.count("concat=n=3:v=1:a=1") == 1
    assert "xfade" not in graph
    assert graph.count("overlay=") == 2
    assert command[command.index("-map") + 1] == "[vo1]"


def test_the_sweep_is_offset_by_whole_frames():
    """tpad takes a frame count. A start expressed in seconds would sooner or
    later round onto the wrong frame and slide the cut out from under the
    covered span."""
    segments = _segments(3)
    boundaries = plan_boundaries(segments, SWEEP)
    graph = _graph(build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS))
    assert "tpad=start=429:start_mode=add:color=black@0" in graph
    assert "tpad=start=879:start_mode=add:color=black@0" in graph


def test_the_sweep_branch_keeps_its_alpha():
    """scale negotiates its output format with whatever is downstream and will
    drop to an alpha-less one, which silently turns the stinger into an opaque
    rectangle. format=rgba after the scale is what prevents it."""
    segments = _segments(2)
    boundaries = plan_boundaries(segments, SWEEP)
    graph = _graph(build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS))
    sweep_branch = [part for part in graph.split(";") if "tpad=" in part][0]
    assert sweep_branch.count("format=rgba") == 2
    assert sweep_branch.index("scale=") < sweep_branch.rindex("format=rgba")


def test_the_overlay_passes_the_picture_through_after_the_sweep_ends():
    """Without eof_action=pass, overlay repeats the sweep's last frame over
    everything that follows it."""
    segments = _segments(2)
    boundaries = plan_boundaries(segments, SWEEP)
    graph = _graph(build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS))
    assert graph.count("eof_action=pass") == 1


def test_a_movie_of_bare_cuts_maps_the_concat_output():
    segments = _segments(3)
    boundaries = plan_boundaries(segments, None)
    command = build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS)
    assert command.count("-i") == 3
    assert command[command.index("-map") + 1] == "[vc]"


def test_frames_convert_to_whole_audio_samples():
    """48000 / 30 == 1600 exactly, so no frame lands mid-sample."""
    segments = _segments(3)
    boundaries = plan_boundaries(segments, SWEEP)
    graph = _graph(build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS))
    assert "atrim=end_sample=720000" in graph  # 450 frames * 1600


def test_an_unplaced_boundary_fails_clearly():
    """An unplaced boundary would drop its sweep at the front of the movie,
    a second away from the cut it is supposed to hide -- far harder to notice
    in a finished render than an outright failure."""
    segments = _segments(2)
    boundaries = decide_boundaries(segments, SWEEP)  # decided, never placed
    with pytest.raises(RenderError, match="not placed"):
        build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS)


def test_a_boundary_count_that_cannot_join_the_segments_fails_clearly():
    segments = _segments(3)
    with pytest.raises(RenderError, match="cannot join"):
        build_chain_command(segments, [], Path("o.mp4"), OPTIONS)


def test_every_pass_two_input_is_an_encoded_stream():
    """Regression: sting inputs used to be looped multi-megapixel PNGs, which
    the chained xfades buffered at ~33MB a frame. A seven-clip movie was killed
    by the OOM killer. Nothing in pass 2 may be a still image again."""
    segments = _segments(4)
    boundaries = plan_boundaries(segments, SWEEP)
    command = build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS)

    assert "-loop" not in command, "a looped still image is still being decoded in pass 2"
    assert not any(str(argument).endswith(".png") for argument in command), command


def test_a_signal_death_is_reported_as_such_not_as_ffmpeg_output(monkeypatch):
    """Regression: a container OOM kill surfaced as 2000 characters of ffmpeg
    progress output, because `frame=...` lines had pushed everything useful out
    of the captured tail. The cause was invisible in the recorded error."""
    import subprocess

    from backend.app.services import renderer as renderer_module

    def killed(*args, **kwargs):
        raise subprocess.CalledProcessError(
            -9, "ffmpeg", output="", stderr="frame= 481 fps=13 q=26.0 size=20992KiB"
        )

    monkeypatch.setattr(renderer_module.subprocess, "run", killed)

    with pytest.raises(RenderError) as caught:
        renderer_module._run(["ffmpeg"], "concat")

    message = str(caught.value)
    assert "SIGKILL" in message
    assert "memory" in message
    assert "frame=" not in message, "progress spam is still being surfaced"
    assert caught.value.stage == "concat"


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------
def test_chunking_cannot_change_which_cuts_carry_a_sweep():
    """Boundaries are decided against the two adjacent clips alone, never
    against where the cut falls in the finished movie, so a decision cannot
    depend on how the joins are later grouped."""
    segments = [Segment(Path(f"c{i}"), frames) for i, frames in enumerate([450, 30, 450, 450])]
    decided = decide_boundaries(segments, SWEEP)

    assert [b.index for b in decided] == [0, 1, 2]
    assert [b.hard_cut for b in decided] == [True, True, False]


def test_placing_the_same_boundaries_against_different_segments():
    """Chunked rendering places a boundary twice: inside its group, and between
    group outputs. Placement must follow the segments it is placed against."""
    decided = decide_boundaries(_segments(3), SWEEP)

    place_boundaries(_segments(3, frames=450), decided)
    inner = [b.start for b in decided]
    place_boundaries(_segments(3, frames=900), decided)
    outer = [b.start for b in decided]

    assert inner == [429, 879]
    assert outer == [879, 1779]


def test_a_sweep_survives_being_placed_at_the_front_of_a_group():
    """Within a group the running total restarts, so the first boundary's sweep
    starts at the first clip's length minus the cover frame. Requiring both
    neighbours to be a full sweep long is what keeps that non-negative."""
    group = _segments(2, frames=44)
    placed = place_boundaries(group, decide_boundaries(group, SWEEP))
    assert placed[0].start >= 0


def _stub_two_clip_render(tmp_path, monkeypatch, probe):
    """Keep the render_movie entry point real while replacing costly ffmpeg passes."""
    from backend.app.filename_parser import ParsedClipName
    from backend.app.services import renderer as renderer_module

    monkeypatch.setattr(renderer_module, "load_sweep", lambda options: None)
    monkeypatch.setattr(
        renderer_module, "normalize_clip",
        lambda spec, destination, workdir, options: Segment(destination, 60),
    )

    def join(segments, boundaries, output, options, workdir, **kwargs):
        output.write_bytes(b"nonempty output")

    def final_probe(path):
        assert path.name == "output.mp4"
        assert path.read_bytes() == b"nonempty output"
        assert not (tmp_path / "out.mp4").exists(), "the file was published before verification"
        return probe

    monkeypatch.setattr(renderer_module, "join_segments", join)
    monkeypatch.setattr(renderer_module, "probe_media", final_probe)
    return [
        renderer_module.ClipSpec(
            source=tmp_path / f"source-{index}.mp4",
            name=ParsedClipName("Falcons", f"player{index}", "Gold", "mp4"),
        )
        for index in range(2)
    ]


@pytest.mark.parametrize(
    "probe",
    [
        {"duration": None, "has_video": True, "has_audio": True, "nb_frames": 120},
        {"duration": 4.0, "has_video": False, "has_audio": True, "nb_frames": 120},
        {"duration": 4.0, "has_video": True, "has_audio": False, "nb_frames": 120},
        {"duration": 4.0, "has_video": True, "has_audio": True, "nb_frames": 119},
        {"duration": 4.0, "has_video": True, "has_audio": True, "nb_frames": None},
    ],
    ids=["no-duration", "no-video", "no-audio", "short-video", "unknown-frame-count"],
)
def test_render_movie_rejects_unverified_final_output(tmp_path, monkeypatch, probe):
    """Failed verification must discard the scratch MP4, not publish it."""
    from backend.app.services import renderer as renderer_module

    clips = _stub_two_clip_render(tmp_path, monkeypatch, probe)
    output = tmp_path / "out.mp4"
    with pytest.raises(RenderError) as caught:
        renderer_module.render_movie(clips, output, OPTIONS)
    assert caught.value.stage == "verify"
    assert not output.exists()


def test_render_movie_reports_verified_final_frames(tmp_path, monkeypatch):
    from backend.app.services import renderer as renderer_module

    clips = _stub_two_clip_render(
        tmp_path, monkeypatch,
        {"duration": 4.0, "has_video": True, "has_audio": True, "nb_frames": 120},
    )
    media = renderer_module.render_movie(clips, tmp_path / "out.mp4", OPTIONS)
    assert media["frames"] == media["nb_frames"] == 120
    assert media["expected_duration"] == 4.0
    assert (tmp_path / "out.mp4").read_bytes() == b"nonempty output"
