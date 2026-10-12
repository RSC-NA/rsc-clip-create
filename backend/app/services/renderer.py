"""Two-pass FFmpeg renderer.

Pass 1 normalizes every clip to one profile and burns its label. Pass 2 joins
the normalized clips with hard cuts and overlays the brand sweep across each
one, so the cut lands while the sweep hides the picture completely.

The sweep is a stinger with an alpha channel: transparent, ramping up to fully
opaque, then breaking away again. It is composited ON TOP of the joined
timeline rather than spliced into it, which is why a movie's length is exactly
the sum of its clips -- a transition costs no time. Splicing it in as its own
segment, the way the old generated wedge card was, would flatten its
transparent head and tail onto black and dip the picture at every join.

All timing is planned in INTEGER FRAMES, never floating-point seconds. Frame
counts are what ``trim=end_frame`` and ``tpad=start`` take, and at 30fps/48kHz
one frame is exactly 1600 samples, so a frame-based plan is simultaneously
exact for video and audio.
"""

from __future__ import annotations

import logging
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Sequence

from backend.app.config import get_settings
from backend.app.filename_parser import ParsedClipName
from backend.app.services import brand
from backend.app.storage import ProbeError, probe_media


logger = logging.getLogger(__name__)
settings = get_settings()


class RenderError(RuntimeError):
    def __init__(self, message: str, stage: str = "render"):
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class ClipSpec:
    source: Path
    name: ParsedClipName


@dataclass(frozen=True)
class RenderOptions:
    movie_id: str
    fps: int = 30
    width: int = 1920
    height: int = 1080
    # The stinger overlaid across every cut. None means the bundled sweep;
    # an account that has uploaded its own passes that path instead.
    sweep_path: Path | None = None
    font_path: Path | None = None
    font_size: int = 56
    stroke_width: int = 3

    @property
    def samples_per_frame(self) -> int:
        return 48000 // self.fps

    @property
    def sweep(self) -> Path:
        return self.sweep_path or brand.BUNDLED_SWEEP


@dataclass(frozen=True)
class Segment:
    path: Path
    frames: int


@dataclass(frozen=True)
class Sweep:
    """The brand sweep, measured against the output profile."""

    path: Path
    frames: int
    # The frame within the sweep where it hides the picture, and so where the
    # clips cut. Everything before it overlays the outgoing clip; everything
    # from it on overlays the incoming one.
    cover: int

    @property
    def lead_frames(self) -> int:
        """How much of the outgoing clip the sweep covers."""
        return self.cover

    @property
    def tail_frames(self) -> int:
        """How much of the incoming clip the sweep covers."""
        return self.frames - self.cover


@dataclass
class Boundary:
    """One cut between two clips, with the sweep that hides it."""

    index: int
    sweep: Sweep | None = None
    # Frame in the joined timeline where the sweep starts. None until
    # place_boundaries resolves it against the segment list being joined --
    # deciding a boundary and placing it are separate steps, and a placement
    # is only meaningful against one particular list.
    start: int | None = None

    @property
    def hard_cut(self) -> bool:
        """A cut with no sweep over it. Adjacent clips were too short."""
        return self.sweep is None


def _frames_to_seconds(frames: int, fps: int) -> str:
    return f"{frames / fps:.6f}"


def _run(command: list[str], stage: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=settings.render_ffmpeg_timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RenderError("FFmpeg and FFprobe must be installed on the worker.", stage) from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError(
            f"FFmpeg timed out after {settings.render_ffmpeg_timeout_seconds}s.", stage
        ) from exc
    except subprocess.CalledProcessError as exc:
        if exc.returncode < 0:
            # Killed by a signal, not an ffmpeg error. -9 is SIGKILL, which in a
            # container is almost always the OOM killer; the captured stderr in
            # that case is just progress output and says nothing useful.
            signal_name = signal.Signals(-exc.returncode).name
            hint = (
                " The renderer was most likely killed for using too much memory."
                " Lower RENDER_CHUNK_SIZE or give the container more memory."
                if -exc.returncode == signal.SIGKILL
                else ""
            )
            raise RenderError(
                f"FFmpeg was killed by {signal_name} during the {stage} stage.{hint}",
                stage,
            ) from exc
        detail = (exc.stderr or exc.stdout or "Media command failed.").strip()[-2000:]
        raise RenderError(detail, stage) from exc


# --------------------------------------------------------------------------
# Pass 1: normalize + label
# --------------------------------------------------------------------------
_NORMALIZE_TAIL = "fps={fps}:round=near,format=yuv420p,setsar=1,settb=1/{tb}"


def normalize_clip(
    spec: ClipSpec, destination: Path, workdir: Path, options: RenderOptions
) -> Segment:
    """Render one clip to the common profile with its label burned in."""
    source = spec.source
    if not source.exists():
        raise RenderError(f"Input clip is missing: {source.name}", "input")

    try:
        media = probe_media(source, strict=True)
    except ProbeError as exc:
        # Never guess. Treating an unreadable probe as "no audio" is what used
        # to silently replace a clip's real audio with silence.
        raise RenderError(str(exc), "probe") from exc

    duration = media.get("duration")
    if not duration or duration <= 0:
        raise RenderError(f"{source.name} has no usable duration.", "probe")
    frames = max(1, int(duration * options.fps))

    label_png = workdir / f"{destination.stem}-label.png"
    brand.create_label_image(
        spec.name,
        label_png,
        font_size=options.font_size,
        font_path=options.font_path,
        stroke_width=options.stroke_width,
        canvas=(options.width, options.height),
    )

    has_audio = bool(media.get("has_audio"))
    seconds = _frames_to_seconds(frames, options.fps)
    tail = _NORMALIZE_TAIL.format(fps=options.fps, tb=options.fps * 1000)

    command = [
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-nostats",
        "-i", str(source),
        # Always present at index 1 so pass 2 can reference an audio stream on
        # every intermediate unconditionally.
        "-f", "lavfi", "-t", seconds, "-i", "anullsrc=r=48000:cl=stereo",
        "-loop", "1", "-framerate", str(options.fps),
        "-t", _frames_to_seconds(frames + options.fps, options.fps), "-i", str(label_png),
        "-filter_complex",
        (
            f"[0:v]scale={options.width}:{options.height}"
            f":force_original_aspect_ratio=decrease:flags=bicubic,"
            f"pad={options.width}:{options.height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration=1,"
            f"{tail},trim=end_frame={frames},setpts=PTS-STARTPTS[base];"
            f"[2:v]format=rgba,setpts=PTS-STARTPTS[label];"
            f"[base][label]overlay=0:0:format=auto:eof_action=pass,{tail}[v];"
            f"[{'0:a:0' if has_audio else '1:a:0'}]aresample=48000:first_pts=0,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
            f"atrim=end_sample={frames * options.samples_per_frame},asetpts=N/SR/TB[a]"
        ),
        "-map", "[v]", "-map", "[a]",
        "-fps_mode", "cfr", "-r", str(options.fps),
        "-c:v", "libx264", "-preset", settings.render_intermediate_preset,
        "-crf", str(settings.render_intermediate_crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", settings.render_audio_bitrate, "-ar", "48000", "-ac", "2",
        str(destination),
    ]
    _run(command, "overlay")

    # Trust the file, not the request: pass 2 re-trims to this number so any
    # imprecision here is corrected rather than accumulated.
    written = probe_media(destination)
    actual = written.get("nb_frames") or frames
    return Segment(path=destination, frames=max(1, min(frames, int(actual))))


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------
def sweep_chain(options: RenderOptions) -> str:
    """Bring the sweep to the output profile, keeping its alpha.

    Shared by the alpha measurement and the join graph so the two cannot drift:
    the frame indices ``cover_frame`` reports are only meaningful if the
    overlay sees exactly the frames that were measured.

    ``format=rgba`` appears twice deliberately. The first pins the decoder's
    output for ``scale``; the second pins ``scale``'s own output, which is
    otherwise negotiated with whatever is downstream -- and ``scale`` will
    happily drop to an alpha-less format, which makes ``alphaextract``
    unsatisfiable and fails the whole graph.
    """
    return (
        f"format=rgba,fps={options.fps}:round=near,"
        f"scale={options.width}:{options.height}"
        f":force_original_aspect_ratio=decrease:flags=bicubic,"
        f"pad={options.width}:{options.height}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        f"setsar=1,format=rgba,settb=1/{options.fps * 1000}"
    )


def _alpha_by_frame(path: Path, options: RenderOptions) -> list[int]:
    """One mean-alpha byte per frame of the sweep, at the output frame rate.

    Scaling the extracted alpha plane to 1x1 is the measurement: ``area``
    scaling averages every pixel, so 255 means the frame covers the picture
    completely.
    """
    command = [
        "ffmpeg", "-v", "error", "-nostdin", "-nostats",
        "-i", str(path),
        "-filter_complex",
        f"[0:v]{sweep_chain(options)},alphaextract,format=gray,scale=1:1:flags=area[a]",
        "-map", "[a]", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=True,
            timeout=settings.render_ffmpeg_timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # A sweep with no alpha channel lands here, as does an unreadable file.
        # Neither should fail a render: cover_frame falls back to the midpoint.
        logger.warning("could not measure the sweep's alpha (%s); assuming it covers at its midpoint", exc)
        return []
    return list(result.stdout)


def load_sweep(options: RenderOptions) -> Sweep | None:
    """Measure the configured sweep. Never raises.

    A missing, unreadable or zero-length sweep yields ``None`` and every cut
    renders bare, exactly as it does for clips too short to carry one. A
    transition is decoration; it must never be the reason a render fails.
    """
    path = options.sweep
    if not path.is_file():
        logger.warning("brand sweep is missing at %s; cuts will render bare", path)
        return None

    media = probe_media(path)
    duration = media.get("duration")
    if not media.get("has_video") or not duration or duration <= 0:
        logger.warning("brand sweep %s has no usable video; cuts will render bare", path)
        return None

    frames = max(1, round(duration * options.fps))
    alpha = _alpha_by_frame(path, options)
    if alpha:
        # Trust the decoded frame count over the container's duration.
        frames = len(alpha)
    cover = brand.cover_frame(alpha) if alpha else frames // 2
    logger.info(
        "brand sweep %s: %d frames at %dfps, cutting at frame %d",
        path.name, frames, options.fps, cover,
    )
    return Sweep(path=path, frames=frames, cover=cover)


# --------------------------------------------------------------------------
# Pass 2: plan the joins
# --------------------------------------------------------------------------
def decide_boundaries(
    segments: Sequence[Segment], sweep: Sweep | None
) -> list[Boundary]:
    """Choose which cuts carry a sweep, without placing them in a timeline.

    The test is on the two adjacent clips alone, never on where the cut falls
    in the finished movie, because chunked rendering places the same boundary
    against two different segment lists. Requiring both neighbours to be at
    least a full sweep long guarantees, in any grouping, that the sweep starts
    at or after the group's first frame, ends at or before its last, and never
    collides with the sweep on the far side of a clip.
    """
    boundaries: list[Boundary] = []
    for index in range(max(0, len(segments) - 1)):
        room = min(segments[index].frames, segments[index + 1].frames)
        if sweep is None or room < sweep.frames:
            if sweep is not None:
                logger.warning(
                    "cut %d renders bare: a %d-frame sweep needs %d frames of clip "
                    "on each side, and the shorter neighbour has %d",
                    index, sweep.frames, sweep.frames, room,
                )
            boundaries.append(Boundary(index=index))
            continue
        boundaries.append(Boundary(index=index, sweep=sweep))
    return boundaries


def place_boundaries(
    segments: Sequence[Segment], boundaries: Sequence[Boundary]
) -> list[Boundary]:
    """Resolve each sweep's start frame against a given segment list.

    Separate from deciding a boundary because chunked rendering places the same
    boundary twice: once inside its group, and once between group outputs. The
    decision must not change when the grouping does.

        C_i     = sum of segment lengths up to and including i
        start_i = C_i - cover
    """
    if len(segments) < 2:
        return []
    if len(boundaries) != len(segments) - 1:
        raise RenderError(
            f"{len(boundaries)} boundaries cannot join {len(segments)} segments.",
            "setup",
        )

    running = segments[0].frames
    for index, boundary in enumerate(boundaries):
        if boundary.sweep is not None:
            boundary.start = running - boundary.sweep.cover
        running += segments[index + 1].frames
    return list(boundaries)


def plan_boundaries(
    segments: Sequence[Segment], sweep: Sweep | None
) -> list[Boundary]:
    """Decide and place every cut. The single-pass path uses this directly."""
    return place_boundaries(segments, decide_boundaries(segments, sweep))


def total_frames(segments: Sequence[Segment], boundaries: Sequence[Boundary]) -> int:
    """Overlaying a transition costs no time, so this is just the clips.

    ``boundaries`` is accepted so callers need not care which transition model
    is in force; it deliberately does not affect the result.
    """
    return sum(segment.frames for segment in segments)


# --------------------------------------------------------------------------
# Pass 2: build the graph
# --------------------------------------------------------------------------
def build_chain_command(
    segments: Sequence[Segment],
    boundaries: Sequence[Boundary],
    output: Path,
    options: RenderOptions,
) -> list[str]:
    """Pure function: the full ffmpeg argv for the join pass.

    Kept free of side effects so the filter graph -- the part most likely to be
    subtly wrong -- can be asserted in a unit test without running ffmpeg.
    """
    fps, spf = options.fps, options.samples_per_frame
    tail = _NORMALIZE_TAIL.format(fps=fps, tb=fps * 1000)

    if len(boundaries) != max(0, len(segments) - 1):
        raise RenderError(
            f"{len(boundaries)} boundaries cannot join {len(segments)} segments.",
            "setup",
        )

    inputs: list[str] = []
    graph: list[str] = []
    for segment in segments:
        inputs += ["-i", str(segment.path)]

    # The sweep is opened once per cut rather than split from a single input.
    # A split holds every frame until its last branch consumes it, and those
    # branches are consumed seconds apart in the output; separate inputs each
    # sit idle behind their own tpad and decode only when their turn arrives.
    swept = [b for b in boundaries if not b.hard_cut]
    for boundary in swept:
        assert boundary.sweep is not None
        inputs += ["-i", str(boundary.sweep.path)]

    # Normalize every branch identically -- concat rejects mismatched size, SAR
    # or timebase.
    for position, segment in enumerate(segments):
        graph.append(
            f"[{position}:v]{tail},trim=end_frame={segment.frames},"
            # fps is re-asserted AFTER setpts: setpts clears the link's
            # frame-rate metadata, and ffmpeg 7.x's concat and overlay refuse an
            # input whose rate reads as 1/0. 8.x tolerates it, so this only
            # shows up off the development machine.
            f"setpts=PTS-STARTPTS,fps={fps}[v{position}]"
        )
        graph.append(
            f"[{position}:a]aresample=48000:first_pts=0,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
            f"atrim=end_sample={segment.frames * spf},asetpts=N/SR/TB[a{position}]"
        )

    if len(segments) == 1:
        video, audio = "v0", "a0"
    else:
        pairs = "".join(f"[v{p}][a{p}]" for p in range(len(segments)))
        graph.append(f"{pairs}concat=n={len(segments)}:v=1:a=1[vc][ac]")
        video, audio = "vc", "ac"

    # Every cut is hard; the sweeps are composited over the joined timeline.
    # tpad takes a frame COUNT, so the placement stays exact -- a start
    # expressed in seconds would round onto the wrong frame sooner or later.
    for position, boundary in enumerate(swept):
        assert boundary.sweep is not None
        if boundary.start is None or boundary.start < 0:
            # Unplaced, or placed against a different segment list. Either way
            # the sweep would land somewhere other than on its cut, and a
            # transition drifting a second off its join is much harder to spot
            # in a finished movie than a failed render.
            raise RenderError(
                f"Boundary {boundary.index} is not placed on this timeline; "
                "place_boundaries() must run against these segments.",
                "setup",
            )
        graph.append(
            f"[{len(segments) + position}:v]{sweep_chain(options)},"
            f"tpad=start={boundary.start}:start_mode=add:color=black@0,"
            f"fps={fps}[sw{position}]"
        )
        # eof_action=pass: the sweep is far shorter than the movie, and without
        # this overlay would freeze its last frame over everything after it.
        graph.append(
            f"[{video}][sw{position}]overlay=0:0:format=auto:eof_action=pass[vo{position}]"
        )
        video = f"vo{position}"

    return [
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-nostats",
        *inputs,
        "-filter_complex", ";".join(graph),
        "-map", f"[{video}]", "-map", f"[{audio}]",
        "-fps_mode", "cfr", "-r", str(fps),
        "-c:v", "libx264", "-preset", settings.render_final_preset,
        "-crf", str(settings.render_final_crf), "-pix_fmt", "yuv420p",
        "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", settings.render_audio_bitrate, "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(output),
    ]


def _measure(path: Path, fallback: int) -> int:
    probed = probe_media(path)
    actual = probed.get("nb_frames")
    return int(actual) if actual else fallback


def _render_group(
    segments: Sequence[Segment],
    boundaries: Sequence[Boundary],
    output: Path,
    options: RenderOptions,
) -> Segment:
    """Join one run of segments and measure the result."""
    placed = place_boundaries(segments, boundaries)
    _run(build_chain_command(segments, placed, output, options), "concat")
    expected = total_frames(segments, placed)
    return Segment(path=output, frames=_measure(output, expected))


def join_segments(
    segments: Sequence[Segment],
    boundaries: Sequence[Boundary],
    output: Path,
    options: RenderOptions,
    workdir: Path,
    *,
    depth: int = 0,
    on_progress: Callable[[], None] | None = None,
) -> Segment:
    """Join every segment, in groups small enough to survive.

    Overlaying the sweep is far cheaper than the chained xfades this used to
    do -- those buffered about 0.8GB per clip and were OOM-killed at seven
    clips -- but grouping is kept because it also bounds the number of open
    decoders, and because a boundary's decision is already independent of it.

    Segments are joined in groups of ``render_chunk_size`` and the group
    outputs joined again, recursively. A sweep that straddles a group seam is
    simply placed at the outer level, against the group outputs.

    ``on_progress`` fires after every group. Joining is the long half of a
    render now that there are no stings to encode -- measured at 78s of a 104s
    six-clip job -- and without a ping here the whole of it passes with the
    heartbeat untouched, which is what ``requeue_stale_jobs`` reads as a dead
    worker.
    """
    chunk = max(2, settings.render_chunk_size)
    if len(segments) <= chunk:
        joined = _render_group(segments, boundaries, output, options)
        if on_progress is not None:
            on_progress()
        return joined

    groups: list[Segment] = []
    between: list[Boundary] = []
    position = 0
    index = 0
    while position < len(segments):
        run = list(segments[position : position + chunk])
        inner = list(boundaries[position : position + len(run) - 1])

        if len(run) == 1:
            groups.append(run[0])
        else:
            destination = workdir / f"group-d{depth}-{index:04d}.mp4"
            logger.info(
                "joining group %d at depth %d (%d segments)", index, depth, len(run)
            )
            groups.append(_render_group(run, inner, destination, options))
            if on_progress is not None:
                on_progress()

        joiner = position + len(run) - 1
        if joiner < len(boundaries):
            between.append(boundaries[joiner])
        position += len(run)
        index += 1

    return join_segments(
        groups, between, output, options, workdir,
        depth=depth + 1, on_progress=on_progress,
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def render_movie(
    clips: Sequence[ClipSpec],
    output: Path,
    options: RenderOptions | None = None,
    on_progress: Callable[[], None] | None = None,
) -> dict:
    """Normalize every clip, then join them under the brand sweep.

    Returns the probed metadata of the finished file, enriched with where each
    sweep landed so the admin console and tests can inspect it.
    """
    if not clips:
        raise RenderError("A render needs at least one clip.", "input")
    options = options or RenderOptions(movie_id=output.stem)

    # 2n-1 inputs are opened at once in pass 2 -- one per clip, one per swept
    # cut. Guard the descriptor/decoder budget with a clear error rather than
    # an opaque ffmpeg failure.
    if len(clips) > settings.render_max_graph_inputs:
        raise RenderError(
            f"This movie has {len(clips)} clips; the renderer joins at most "
            f"{settings.render_max_graph_inputs} in one pass.",
            "input",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    sweep = load_sweep(options)
    with tempfile.TemporaryDirectory(prefix="clip-create-") as temporary:
        workdir = Path(temporary)
        segments = []
        for index, spec in enumerate(clips):
            segments.append(
                normalize_clip(spec, workdir / f"clip-{index:04d}.mp4", workdir, options)
            )
            # Pass 1 is the long part; let the caller prove it is still alive.
            if on_progress is not None:
                on_progress()
        # Decided once, against the original clips, so the choices do not
        # depend on how the joins are later grouped.
        boundaries = decide_boundaries(segments, sweep)
        expected = total_frames(segments, boundaries)
        # Where each sweep sits in the FINISHED movie. Captured now, because
        # join_segments re-places a seam boundary against the group outputs it
        # ends up joining, which leaves a group-relative start on the object.
        placement = [boundary.start for boundary in place_boundaries(segments, boundaries)]
        logger.info(
            "joining %d clips with %d swept cuts (%d expected frames, %.3fs)",
            len(segments), sum(0 if b.hard_cut else 1 for b in boundaries),
            expected, expected / options.fps,
        )
        # Keep the file in the temporary directory until final verification
        # succeeds. A failed probe must not publish an unreferenced MP4.
        scratch = workdir / "output.mp4"
        join_segments(
            segments, boundaries, scratch, options, workdir, on_progress=on_progress
        )
        if not scratch.exists() or scratch.stat().st_size == 0:
            raise RenderError("FFmpeg did not produce an output file.", "verify")
        media = probe_media(scratch)
        if media.get("duration") is None or not media.get("has_video") or not media.get("has_audio"):
            raise RenderError("The rendered output is missing duration, video, or audio.", "verify")
        actual_frames = media.get("nb_frames")
        if actual_frames is None or actual_frames != expected:
            raise RenderError(
                f"The rendered output has {actual_frames} video frames; expected {expected}.",
                "verify",
            )
        shutil.move(str(scratch), str(output))

    media["frames"] = actual_frames
    media["expected_duration"] = expected / options.fps
    media["sweep"] = (
        {"source": sweep.path.name, "frames": sweep.frames, "cover": sweep.cover}
        if sweep
        else None
    )
    media["transitions"] = [
        {"boundary": b.index, "start": start}
        if not b.hard_cut
        else {"boundary": b.index, "hard_cut": True}
        for b, start in zip(boundaries, placement)
    ]
    return media
