from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from backend.app.filename_parser import ParsedClipName
from backend.app.storage import probe_media


class RenderError(RuntimeError):
    def __init__(self, message: str, stage: str = "render"):
        super().__init__(message)
        self.stage = stage


def _run(command: list[str], stage: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise RenderError("FFmpeg and FFprobe must be installed on the worker.", stage) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "Media command failed.").strip()[-2000:]
        raise RenderError(detail, stage) from exc
    return result


def _font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, 42)
    return ImageFont.load_default()


def _create_label_image(name: ParsedClipName, destination: Path) -> None:
    image = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = _font()
    text = name.label
    left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font, spacing=8)
    text_width = right - left
    text_height = bottom - top
    box_left = 22
    box_top = 1080 - text_height - 62
    box_right = min(1898, box_left + text_width + 36)
    box_bottom = 1060
    draw.rounded_rectangle((box_left, box_top, box_right, box_bottom), radius=12, fill=(0, 0, 0, 140))
    draw.multiline_text((40, box_top + 18), text, font=font, fill=(255, 255, 255, 255), spacing=8)
    image.save(destination, format="PNG")


def render_clip(source: Path, destination: Path, name: ParsedClipName, workdir: Path) -> None:
    if not source.exists():
        raise RenderError(f"Input clip is missing: {source.name}", "input")
    label_file = workdir / f"{destination.stem}.png"
    _create_label_image(name, label_file)
    metadata = probe_media(source)
    command = ["ffmpeg", "-y", "-i", str(source)]
    if not metadata.get("has_audio"):
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    label_input_index = 2 if not metadata.get("has_audio") else 1
    command += ["-loop", "1", "-i", str(label_file)]
    command += [
        "-filter_complex",
        f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black[base];"
        f"[{label_input_index}:v]format=rgba[label];[base][label]overlay=0:0:shortest=1[v]",
        "-map", "[v]", "-map", "0:a:0?" if metadata.get("has_audio") else "1:a:0",
        "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(destination),
    ]
    _run(command, "overlay")


def render_movie(clips: list[tuple[Path, ParsedClipName]], output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clip-create-") as temporary:
        workdir = Path(temporary)
        intermediates: list[Path] = []
        for index, (source, name) in enumerate(clips):
            destination = workdir / f"clip-{index:04d}.mp4"
            render_clip(source, destination, name, workdir)
            intermediates.append(destination)

        concat_file = workdir / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{path}'" for path in intermediates) + "\n",
            encoding="utf-8",
        )
        _run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output)],
            "concat",
        )

    if not output.exists() or output.stat().st_size == 0:
        raise RenderError("FFmpeg did not produce an output file.", "verify")
    media = probe_media(output)
    if media.get("duration") is None:
        raise RenderError("The rendered output could not be verified.", "verify")
    return media
