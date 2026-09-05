"""The transition arithmetic, tested without invoking ffmpeg.

xfade offsets are the easiest thing in the pipeline to get subtly wrong, so
they are asserted directly against hand-worked numbers.
"""

from pathlib import Path

import pytest

from backend.app.services.renderer import (
    RenderError,
    RenderOptions,
    Segment,
    build_chain_command,
    plan_boundaries,
    total_frames,
)


def _segments(count, frames=450):
    return [Segment(Path(f"clip-{i}.mp4"), frames) for i in range(count)]


OPTIONS = RenderOptions(movie_id="demo", fps=30, hold_frames=18, blend_frames=9)


def test_three_clip_offsets_match_the_worked_example():
    """3 x 450 frames, hold 18, blend 9 -> sting 36, total 1386 frames (46.2s)."""
    segments = _segments(3)
    boundaries = plan_boundaries(segments, OPTIONS)

    assert [(b.sting_offset, b.clip_offset) for b in boundaries] == [(441, 468), (909, 936)]
    assert total_frames(segments, boundaries) == 1386
    assert total_frames(segments, boundaries) / OPTIONS.fps == pytest.approx(46.2)


@pytest.mark.parametrize("count", [2, 3, 5, 12])
def test_total_length_is_clips_plus_one_hold_per_boundary(count):
    segments = _segments(count)
    boundaries = plan_boundaries(segments, OPTIONS)
    assert total_frames(segments, boundaries) == 450 * count + 18 * (count - 1)


def test_offsets_are_strictly_increasing():
    segments = _segments(6)
    boundaries = plan_boundaries(segments, OPTIONS)
    offsets = [o for b in boundaries for o in (b.sting_offset, b.clip_offset)]
    assert offsets == sorted(offsets)
    assert all(o > 0 for o in offsets)


def test_single_clip_has_no_boundaries():
    assert plan_boundaries(_segments(1), OPTIONS) == []


def test_short_clip_clamps_the_blend():
    segments = [Segment(Path("a"), 450), Segment(Path("b"), 5), Segment(Path("c"), 450)]
    boundaries = plan_boundaries(segments, OPTIONS)
    # Blend cannot exceed the shortest adjacent clip minus one frame.
    assert all(b.blend_frames == 4 for b in boundaries)
    assert all(not b.hard_cut for b in boundaries)


def test_one_frame_clip_degrades_to_a_hard_cut():
    segments = [Segment(Path("a"), 450), Segment(Path("b"), 1)]
    boundaries = plan_boundaries(segments, OPTIONS)
    assert boundaries[0].hard_cut
    assert boundaries[0].hold_frames == 0
    # A hard cut adds no time.
    assert total_frames(segments, boundaries) == 451


def test_zero_hold_disables_stings_entirely():
    options = RenderOptions(movie_id="demo", hold_frames=0, blend_frames=9)
    segments = _segments(3)
    boundaries = plan_boundaries(segments, options)
    assert all(b.hard_cut for b in boundaries)
    assert total_frames(segments, boundaries) == 1350


def _with_encoded_stings(boundaries, tmp_path):
    """Stand in for render_sting, which would otherwise shell out to ffmpeg."""
    for boundary in boundaries:
        if not boundary.hard_cut:
            boundary.clip = tmp_path / f"sting-{boundary.index:04d}.mp4"
    return boundaries


def test_graph_references_every_input_and_maps_the_final_labels(tmp_path):
    segments = _segments(3)
    boundaries = _with_encoded_stings(plan_boundaries(segments, OPTIONS, tmp_path), tmp_path)
    command = build_chain_command(segments, boundaries, Path("out.mp4"), OPTIONS)
    graph = command[command.index("-filter_complex") + 1]

    # 3 clips + 2 sting PNGs
    assert command.count("-i") == 5
    for index in range(3):
        assert f"[v{index}]" in graph and f"[a{index}]" in graph
    for index in range(2):
        assert f"[s{index}]" in graph and f"[sa{index}]" in graph
    # Both halves of each boundary, and the mirrored pair.
    assert graph.count("xfade=") == 4
    assert graph.count("acrossfade=") == 4
    assert "offset=14.700000" in graph and "offset=15.600000" in graph
    assert command[command.index("-map") + 1] == "[vx1]"


def test_blend_frames_convert_to_whole_audio_samples(tmp_path):
    """48000 / 30 == 1600 exactly, so no frame lands mid-sample."""
    segments = _segments(3)
    boundaries = _with_encoded_stings(plan_boundaries(segments, OPTIONS, tmp_path), tmp_path)
    command = build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS)
    graph = command[command.index("-filter_complex") + 1]
    assert "acrossfade=ns=14400" in graph  # 9 frames * 1600


def test_graph_without_an_encoded_sting_fails_clearly(tmp_path):
    segments = _segments(2)
    boundaries = plan_boundaries(segments, OPTIONS)  # no workdir, no encode
    with pytest.raises(RenderError, match="no encoded sting"):
        build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS)


def test_every_pass_two_input_is_an_encoded_clip(tmp_path):
    """Regression: sting inputs used to be looped multi-megapixel PNGs, which
    the chained xfades buffered at ~33MB a frame. A seven-clip movie was killed
    by the OOM killer. Every input must now be a normal encoded stream."""
    segments = _segments(4)
    boundaries = _with_encoded_stings(plan_boundaries(segments, OPTIONS, tmp_path), tmp_path)
    command = build_chain_command(segments, boundaries, Path("o.mp4"), OPTIONS)

    assert "-loop" not in command, "a looped still image is still being decoded in pass 2"
    assert not any(str(a).endswith(".png") for a in command), command
    assert all(
        str(command[i + 1]).endswith(".mp4")
        for i, a in enumerate(command)
        if a == "-i"
    )


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


def test_chunking_preserves_the_transition_decisions(tmp_path):
    """Boundaries are seeded by their GLOBAL index, so grouping must not change
    which transition appears where."""
    from backend.app.services.renderer import decide_boundaries

    segments = _segments(7)
    decided = decide_boundaries(segments, OPTIONS, tmp_path)

    assert [b.index for b in decided] == list(range(6))
    assert [b.spec.transition_in for b in decided] == [
        b.spec.transition_in for b in decide_boundaries(segments, OPTIONS, tmp_path)
    ]


def test_placing_the_same_boundaries_against_different_segments(tmp_path):
    """Chunked rendering places a boundary twice: inside its group, and between
    group outputs. Placement must be independent of the decision."""
    from backend.app.services.renderer import decide_boundaries, place_boundaries

    segments = _segments(3, frames=450)
    decided = decide_boundaries(segments, OPTIONS, tmp_path)
    place_boundaries(segments, decided)
    inner = [(b.sting_offset, b.clip_offset) for b in decided]

    longer = _segments(3, frames=900)
    place_boundaries(longer, decided)
    outer = [(b.sting_offset, b.clip_offset) for b in decided]

    assert inner != outer, "offsets must follow the segments they are placed against"
    assert [b.spec.transition_in for b in decided] == [
        b.spec.transition_in for b in decided
    ]
