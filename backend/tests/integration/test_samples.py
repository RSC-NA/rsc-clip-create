"""Render the user-supplied sample footage and leave something to look at.

Skips cleanly until clips are dropped into backend/tests/fixtures/samples/, so
the suite stays green in the meantime. See that directory's README.
"""

import shutil

import pytest

from backend.app.filename_parser import parse_clip_filename
from backend.app.services.renderer import ClipSpec, RenderOptions, render_movie
from backend.app.storage import probe_media
from backend.tests.conftest import requires_ffmpeg
from backend.tests.integration.artifacts import (
    extract_frame,
    frame_plan,
    reset_output,
    write_report,
)


pytestmark = [pytest.mark.integration, requires_ffmpeg]

FPS = 30
HOLD_SECONDS = 0.6


def test_sample_clips_are_named_for_the_parser(sample_clips):
    """Fail loudly and early if the footage is misnamed, rather than deep in a render."""
    problems = []
    for path in sample_clips:
        try:
            parse_clip_filename(path.name)
        except ValueError as exc:
            problems.append(f"{path.name}: {exc}")
    assert not problems, (
        "Sample clips must be named team-player-tier.ext:\n  " + "\n  ".join(problems)
    )


def test_sample_render_matches_the_plan_and_writes_artifacts(sample_clips, make_logo):
    from pathlib import Path

    hold_frames = round(HOLD_SECONDS * FPS)
    options = RenderOptions(
        movie_id="sample-reel",
        fps=FPS,
        hold_frames=hold_frames,
        blend_frames=hold_frames // 2,
        family="sweep",
        wordmark="Sample Reel",
    )

    # Give the first team a logo and leave the rest without one, so both label
    # layouts appear in the output.
    first_team = parse_clip_filename(sample_clips[0].name).team
    logo_path = Path(make_logo(first_team).storage_path)

    specs = []
    for path in sample_clips:
        parsed = parse_clip_filename(path.name)
        specs.append(
            ClipSpec(
                source=path,
                name=parsed,
                logo_path=logo_path if parsed.team == first_team else None,
            )
        )

    directory = reset_output("samples")
    output = directory / "render.mp4"
    media = render_movie(specs, output, options)

    probed = probe_media(output)
    source_frames = []
    for path in sample_clips:
        info = probe_media(path, strict=True)
        source_frames.append(max(1, int(info["duration"] * FPS)))

    expected_frames = sum(source_frames) + hold_frames * (len(sample_clips) - 1)

    # Frames for eyeballing: the middle of every clip and every sting.
    frames = []
    for label, at in frame_plan(source_frames, hold_frames, FPS):
        destination = directory / "frames" / f"{label}.png"
        if extract_frame(output, at, destination):
            frames.append((f"{label} @ {at:.2f}s", destination))

    report = write_report(
        directory,
        title="Sample footage render",
        sources=list(sample_clips),
        plan={
            "clips": len(sample_clips),
            "hold seconds": HOLD_SECONDS,
            "frames": expected_frames,
            "duration": round(expected_frames / FPS, 3),
            "resolution": "1920x1080",
            "transitions": media["transitions"],
        },
        measured={
            "clips": len(sample_clips),
            "hold seconds": HOLD_SECONDS,
            "frames": media["frames"],
            "duration": round(probed["duration"], 3),
            "resolution": f"{probed['width']}x{probed['height']}",
            "transitions": media["transitions"],
        },
        frames=frames,
    )

    print(f"\nArtifacts written to {report.parent.resolve()}")
    print(f"  open {report.resolve()}")

    # The arithmetic must hold to within a frame.
    assert media["frames"] == expected_frames
    assert probed["duration"] == pytest.approx(expected_frames / FPS, abs=2 / FPS)
    assert (probed["width"], probed["height"]) == (1920, 1080)
    assert probed["has_audio"], "the output lost its audio track"
    assert len(frames) >= len(sample_clips), "could not extract a frame for every clip"


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is required")
def test_report_the_shape_of_the_sample_footage(sample_clips):
    """Not an assertion so much as a record: the properties most likely to
    break the normalize pass are exactly the ones testsrc2 never exercises."""
    print("\nSample footage:")
    for path in sample_clips:
        info = probe_media(path, strict=True)
        print(
            f"  {path.name:44} {info.get('width')}x{info.get('height')} "
            f"@ {info.get('frame_rate')}  {info.get('duration'):.2f}s  "
            f"audio={info.get('has_audio')} ({info.get('audio_codec')})"
        )
    assert all(probe_media(p, strict=True).get("has_video") for p in sample_clips)
