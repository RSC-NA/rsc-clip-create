"""Two-pass FFmpeg renderer.

Pass 1 normalizes every clip to one profile and burns its label. Pass 2 joins
the normalized clips with generated blue/orange brand stings in a single
filter graph.

All timing is planned in INTEGER FRAMES, never floating-point seconds. xfade
snaps its offsets to frame boundaries while acrossfade is sample-exact, so a
plan expressed in seconds drifts audio away from video a little at every
boundary and the error accumulates. At 30fps/48kHz one frame is exactly 1600
samples, so a frame-based plan is simultaneously exact for both streams.
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
    hold_frames: int = 18
    blend_frames: int = 9
    family: str = brand.DEFAULT_FAMILY
    # Centred in every sting. Replaces the movie-title wordmark that used to
    # sit there, and the per-team marks that used to sit on the clip labels.
    sting_logo_path: Path | None = None
    sting_logo_scale: float = 0.34
    font_path: Path | None = None
    font_size: int = 56
    stroke_width: int = 3
    blue: str = "#0B5CD5"
    orange: str = "#FF7A18"

    @property
    def samples_per_frame(self) -> int:
        return 48000 // self.fps


@dataclass(frozen=True)
class Segment:
    path: Path
    frames: int


@dataclass
class Boundary:
    """One join between two clips, with its timing already resolved to frames."""

    index: int
    blend_frames: int
    hold_frames: int
    sting_offset: int = 0            # xfade offset, clip -> sting
    clip_offset: int = 0             # xfade offset, sting -> next clip
    spec: brand.StingSpec | None = None
    png: Path | None = None
    clip: Path | None = None

    @property
    def hard_cut(self) -> bool:
        return self.spec is None

    @property
    def sting_frames(self) -> int:
        return self.hold_frames + 2 * self.blend_frames


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
# Pass 2: plan the joins
# --------------------------------------------------------------------------
def place_boundaries(
    segments: Sequence[Segment], boundaries: Sequence[Boundary]
) -> list[Boundary]:
    """Recompute each boundary's xfade offsets against a given segment list.

    Separate from deciding a boundary because chunked rendering places the same
    boundary twice: once inside its group, and once between group outputs. The
    creative decisions are seeded by the GLOBAL boundary index and must not
    change when the grouping does.

        O_sting(i)  = L_i - b_i
        O_clip(i+1) = L_i + d_i
        L_(i+1)     = L_i + d_i + N_(i+1)
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
        following = segments[index + 1]
        if boundary.hard_cut:
            running += following.frames
            continue
        boundary.sting_offset = running - boundary.blend_frames
        boundary.clip_offset = running + boundary.hold_frames
        running += boundary.hold_frames + following.frames
    return list(boundaries)


def decide_boundaries(
    segments: Sequence[Segment], options: RenderOptions, workdir: Path | None = None
) -> list[Boundary]:
    """Choose each join's timing and artwork, without placing it in a timeline."""
    boundaries: list[Boundary] = []
    if len(segments) < 2:
        return boundaries

    for index in range(len(segments) - 1):
        current, following = segments[index], segments[index + 1]
        # Each clip must keep at least one frame that is not mid-transition.
        blend = min(options.blend_frames, current.frames - 1, following.frames - 1)
        hold = options.hold_frames

        if hold <= 0 or blend < 1:
            # Degrade to a hard cut. xfade cannot take duration=0, so these
            # pairs are joined with the concat filter instead.
            boundaries.append(Boundary(index=index, blend_frames=0, hold_frames=0))
            if blend < 1 and hold > 0:
                logger.warning(
                    "boundary %d falls back to a hard cut: adjacent clips are too short",
                    index,
                )
            continue

        spec = brand.plan_sting(
            options.movie_id, index, options.family,
            sting_frames=hold + 2 * blend, fps=options.fps,
            size=(options.width, options.height),
            blue=options.blue, orange=options.orange,
        )
        png = (workdir / f"sting-{index:04d}.png") if workdir else None
        if png is not None:
            brand.create_sting_image(
                spec, png, logo_path=options.sting_logo_path,
                logo_scale=options.sting_logo_scale,
            )
        boundaries.append(
            Boundary(
                index=index, blend_frames=blend, hold_frames=hold, spec=spec, png=png
            )
        )
    return boundaries


def plan_boundaries(
    segments: Sequence[Segment], options: RenderOptions, workdir: Path | None = None
) -> list[Boundary]:
    """Decide and place every join. The single-pass path uses this directly."""
    return place_boundaries(segments, decide_boundaries(segments, options, workdir))


def total_frames(segments: Sequence[Segment], boundaries: Sequence[Boundary]) -> int:
    return sum(s.frames for s in segments) + sum(b.hold_frames for b in boundaries)


# --------------------------------------------------------------------------
# Pass 2: build the graph
# --------------------------------------------------------------------------
def render_sting(boundary: Boundary, destination: Path, options: RenderOptions) -> Path:
    """Encode one sting to the same profile as a normalised clip.

    Pass 2 used to take the sting as a looped PNG and pan it with `crop` inside
    the main graph. Those canvases are up to 3840x2160 RGBA -- 33MB per frame --
    and the chained xfades buffer them, so peak memory grew about 0.8GB per clip
    and a seven-clip movie was killed by the OOM killer at ~4.6GB.

    Encoding the pan here means every pass-2 input is a small, uniform 1080p
    H.264 stream.
    """
    spec = boundary.spec
    if spec is None or boundary.png is None:
        raise RenderError("Cannot render a sting without its artwork.", "setup")

    fps = options.fps
    tail = _NORMALIZE_TAIL.format(fps=fps, tb=fps * 1000)
    command = [
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-nostats",
        # Two frames of slack, then trimmed exactly: the second xfade of a
        # boundary consumes the accumulated stream to its last frame.
        "-loop", "1", "-framerate", str(fps),
        "-t", _frames_to_seconds(boundary.sting_frames + 2, fps),
        "-i", str(boundary.png),
        "-f", "lavfi", "-t", _frames_to_seconds(boundary.sting_frames, fps),
        "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex",
        (
            f"[0:v]crop={options.width}:{options.height}"
            f":x='{spec.pan_x_expr}':y='{spec.pan_y_expr}',{tail},"
            f"trim=end_frame={boundary.sting_frames},setpts=PTS-STARTPTS,fps={fps}[v];"
            f"[1:a]aresample=48000:first_pts=0,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
            f"atrim=end_sample={boundary.sting_frames * options.samples_per_frame},"
            f"asetpts=N/SR/TB[a]"
        ),
        "-map", "[v]", "-map", "[a]",
        "-fps_mode", "cfr", "-r", str(fps),
        "-c:v", "libx264", "-preset", settings.render_intermediate_preset,
        "-crf", str(settings.render_intermediate_crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", settings.render_audio_bitrate, "-ar", "48000", "-ac", "2",
        str(destination),
    ]
    _run(command, "sting")
    return destination


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

    inputs: list[str] = []
    graph: list[str] = []
    input_index = 0
    clip_inputs: list[int] = []
    sting_inputs: dict[int, int] = {}

    for position, segment in enumerate(segments):
        inputs += ["-i", str(segment.path)]
        clip_inputs.append(input_index)
        input_index += 1
        if position < len(boundaries):
            boundary = boundaries[position]
            if not boundary.hard_cut and boundary.clip is not None:
                inputs += ["-i", str(boundary.clip)]
                sting_inputs[position] = input_index
                input_index += 1

    # Normalize every branch identically -- xfade rejects mismatched size, SAR
    # or timebase.
    for position, segment in enumerate(segments):
        idx = clip_inputs[position]
        graph.append(
            f"[{idx}:v]{tail},trim=end_frame={segment.frames},"
            # fps is re-asserted AFTER setpts: setpts clears the link's
            # frame-rate metadata, and ffmpeg 7.x's xfade refuses an input
            # whose rate reads as 1/0. 8.x tolerates it, so this only shows up
            # off the development machine.
            f"setpts=PTS-STARTPTS,fps={fps}[v{position}]"
        )
        graph.append(
            f"[{idx}:a]aresample=48000:first_pts=0,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
            f"atrim=end_sample={segment.frames * spf},asetpts=N/SR/TB[a{position}]"
        )

    for position, boundary in enumerate(boundaries):
        if boundary.hard_cut:
            continue
        spec = boundary.spec
        if position not in sting_inputs or spec is None:
            # plan_boundaries ran without a workdir, or render_sting was never
            # called. Fail clearly rather than with a KeyError deep in the graph.
            raise RenderError(
                f"Boundary {position} has no encoded sting; "
                "plan_boundaries() needs a workdir and render_sting() must run.",
                "setup",
            )
        idx = sting_inputs[position]
        graph.append(
            f"[{idx}:v]{tail},trim=end_frame={boundary.sting_frames},"
            f"setpts=PTS-STARTPTS,fps={fps}[s{position}]"
        )
        graph.append(
            f"[{idx}:a]aresample=48000:first_pts=0,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
            f"atrim=end_sample={boundary.sting_frames * spf},asetpts=N/SR/TB[sa{position}]"
        )

    video, audio = "v0", "a0"
    for position, boundary in enumerate(boundaries):
        nxt = position + 1
        if boundary.hard_cut:
            graph.append(f"[{video}][v{nxt}]concat=n=2:v=1:a=0[vx{position}]")
            graph.append(f"[{audio}][a{nxt}]concat=n=2:v=0:a=1[ax{position}]")
            video, audio = f"vx{position}", f"ax{position}"
            continue

        spec = boundary.spec
        assert spec is not None
        blend = _frames_to_seconds(boundary.blend_frames, fps)
        samples = boundary.blend_frames * spf

        graph.append(
            f"[{video}][s{position}]xfade=transition={spec.transition_in}"
            f":duration={blend}:offset={_frames_to_seconds(boundary.sting_offset, fps)}"
            f"[vs{position}]"
        )
        graph.append(
            f"[vs{position}][v{nxt}]xfade=transition={spec.transition_out}"
            f":duration={blend}:offset={_frames_to_seconds(boundary.clip_offset, fps)}"
            f"[vx{position}]"
        )
        graph.append(f"[{audio}][sa{position}]acrossfade=ns={samples}:c1=tri:c2=tri[as{position}]")
        graph.append(f"[as{position}][a{nxt}]acrossfade=ns={samples}:c1=tri:c2=tri[ax{position}]")
        video, audio = f"vx{position}", f"ax{position}"

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
) -> Segment:
    """Join every segment, in groups small enough to survive.

    One filter graph over all inputs is the obvious implementation and is what
    this did first, but chained xfades buffer heavily: peak memory measured
    1.69GB for two clips and 4.61GB for seven, where the OOM killer took it.
    The cost is the chain, not the inputs.

    So segments are joined in groups of ``render_chunk_size`` and the group
    outputs joined again, recursively. Boundaries keep their global index, so
    the artwork and transition choices do not depend on the grouping.
    """
    chunk = max(2, settings.render_chunk_size)
    if len(segments) <= chunk:
        return _render_group(segments, boundaries, output, options)

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

        joiner = position + len(run) - 1
        if joiner < len(boundaries):
            between.append(boundaries[joiner])
        position += len(run)
        index += 1

    return join_segments(groups, between, output, options, workdir, depth=depth + 1)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def render_movie(
    clips: Sequence[ClipSpec],
    output: Path,
    options: RenderOptions | None = None,
    on_progress: Callable[[], None] | None = None,
) -> dict:
    """Normalize every clip, then join them with brand stings.

    Returns the probed metadata of the finished file, enriched with the
    transition decisions so the admin console and tests can inspect them.
    """
    if not clips:
        raise RenderError("A render needs at least one clip.", "input")
    options = options or RenderOptions(movie_id=output.stem)

    # 2n-1 inputs are opened at once in pass 2. Guard the descriptor/decoder
    # budget with a clear error rather than an opaque ffmpeg failure.
    if len(clips) > settings.render_max_graph_inputs:
        raise RenderError(
            f"This movie has {len(clips)} clips; the renderer joins at most "
            f"{settings.render_max_graph_inputs} in one pass.",
            "input",
        )

    output.parent.mkdir(parents=True, exist_ok=True)
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
        boundaries = decide_boundaries(segments, options, workdir)
        for boundary in boundaries:
            if not boundary.hard_cut:
                boundary.clip = render_sting(
                    boundary, workdir / f"sting-{boundary.index:04d}.mp4", options
                )
                if on_progress is not None:
                    on_progress()
        expected = total_frames(segments, place_boundaries(segments, boundaries))
        logger.info(
            "joining %d clips with %d transitions (%d expected frames, %.3fs)",
            len(segments), sum(0 if b.hard_cut else 1 for b in boundaries),
            expected, expected / options.fps,
        )
        # Render to a scratch path and move into place only on success, so a
        # killed or timed-out ffmpeg does not leave a truncated MP4 that no
        # Video row references and nothing ever cleans up.
        scratch = workdir / "output.mp4"
        join_segments(segments, boundaries, scratch, options, workdir)
        if not scratch.exists() or scratch.stat().st_size == 0:
            raise RenderError("FFmpeg did not produce an output file.", "verify")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(scratch), str(output))

    if not output.exists() or output.stat().st_size == 0:
        raise RenderError("FFmpeg did not produce an output file.", "verify")
    media = probe_media(output)
    if media.get("duration") is None:
        raise RenderError("The rendered output could not be verified.", "verify")

    media["frames"] = expected
    media["expected_duration"] = expected / options.fps
    media["family"] = options.family
    media["transitions"] = [
        b.spec.as_metadata() if b.spec else {"boundary": b.index, "hard_cut": True}
        for b in boundaries
    ]
    return media
