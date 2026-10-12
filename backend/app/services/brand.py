"""Brand artwork for rendered movies: clip labels, and where the sweep lands.

Everything here is pure -- Pillow for the label, arithmetic for the sweep. No
ffmpeg, no database, no filesystem beyond reading a font and writing a PNG --
which makes the decisions unit-testable without rendering a frame of video.

This module used to generate the transition artwork as well: a blue/orange
wedge card, panned by ffmpeg, with the account's brand logo composited in the
middle. Transitions are now one fixed video -- the brand sweep -- overlaid
across a hard cut, so all of that is gone along with the logo it carried.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from backend.app.filename_parser import ParsedClipName


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------
class FontError(RuntimeError):
    """No usable font could be resolved."""


# Vendored so every host renders identically. Anton is SIL OFL; the licence
# ships alongside it. See BUNDLED_FONT_NOTE for why this face and not another.
BUNDLED_FONT = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "Anton-Regular.ttf"

BUNDLED_FONT_NOTE = (
    "Anton stands in for Ultimatum, which is not published on Google Fonts. "
    "Point RENDER_FONT_PATH at an Ultimatum .ttf to use the real thing."
)


def resolve_font(size: int, explicit: Path | None = None) -> ImageFont.FreeTypeFont:
    """Resolve a font deterministically.

    An earlier implementation scanned three host paths, so the same movie
    rendered on macOS (Arial) and Linux (DejaVu) produced different labels, and
    it fell back to a 10px face that was illegible on a 1080p frame. The font
    is now vendored, with RENDER_FONT_PATH as the override.
    """
    if explicit is not None:
        if not Path(explicit).is_file():
            raise FontError(f"Configured render font is missing: {explicit}")
        return ImageFont.truetype(str(explicit), size)

    if BUNDLED_FONT.is_file():
        return ImageFont.truetype(str(BUNDLED_FONT), size)

    logger.warning("bundled font missing at %s; falling back to Pillow's", BUNDLED_FONT)
    font = ImageFont.load_default(size=size)
    if not isinstance(font, ImageFont.FreeTypeFont):
        raise FontError("Pillow was built without FreeType; set RENDER_FONT_PATH.")
    return font


# --------------------------------------------------------------------------
# Clip label
# --------------------------------------------------------------------------
def create_label_image(
    name: ParsedClipName,
    destination: Path,
    *,
    font_size: int = 56,
    font_path: Path | None = None,
    stroke_width: int = 3,
    canvas: tuple[int, int] = (1920, 1080),
) -> None:
    """Full-frame RGBA overlay: bottom-left text, wrapped only when needed, no plate.

    Text only. The brand mark is the sweep that plays over every cut; it has
    never been part of the label since the per-team logo library was removed.

    The text is drawn with a dark stroke rather than sitting on a translucent
    box. Without either, white type disappears over pale footage. Set
    ``stroke_width=0`` for bare glyphs.
    """
    width, height = canvas
    image = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = resolve_font(font_size, font_path)

    text = name.label
    margin_left, margin_bottom = 40, 44
    available_width = width - 2 * margin_left

    def bounds(line: str) -> tuple[int, int, int, int]:
        return draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)

    def fits(line: str) -> bool:
        left, _, right, _ = bounds(line)
        return right - left <= available_width

    if fits(text):
        lines = [text]
    else:
        # Keep the metadata segments separate when wrapping; an uninterrupted
        # segment may itself exceed the frame, so split it at glyph boundaries.
        # Never discard the remainder (in particular the last tier glyph).
        lines = []
        for index, segment in enumerate((name.team, name.player, name.tier)):
            remaining = ("· " if index else "") + segment
            while remaining:
                end = 1
                while end < len(remaining) and fits(remaining[:end + 1]):
                    end += 1
                lines.append(remaining[:end])
                remaining = remaining[end:]

    boxes = [bounds(line) for line in lines]
    line_gap = 8
    total_height = sum(bottom - top for _, top, _, bottom in boxes) + line_gap * (len(lines) - 1)
    line_top = height - margin_bottom - total_height
    for line, (left, top, _, bottom) in zip(lines, boxes):
        draw.text(
            (margin_left - left, line_top - top),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, 190),
        )
        line_top += bottom - top + line_gap
    image.save(destination, format="PNG")


# --------------------------------------------------------------------------
# The brand sweep
# --------------------------------------------------------------------------
# The stinger played over every cut. Vendored so a fresh checkout renders the
# real transition with no setup; RENDER_BRAND_SWEEP_PATH overrides it, and an
# account can upload its own. A replacement needs an alpha channel and must
# reach full opacity somewhere in the middle -- that opaque span is what hides
# the cut. See COVER_THRESHOLD.
BUNDLED_SWEEP = Path(__file__).resolve().parents[1] / "assets" / "stings" / "brand-sweep.mov"

# Mean alpha, 0-255, at or above which a frame counts as hiding the picture.
# Not 255: the bundled sweep's covered frames measure exactly 255 at 1080p, but
# a replacement with a soft edge or a stray transparent pixel would score a
# point or two lower and must not be read as "never covers".
COVER_THRESHOLD = 250


def cover_frame(alpha_by_frame: Sequence[int], *, threshold: int = COVER_THRESHOLD) -> int:
    """The frame of the sweep at which the clips should cut.

    ``alpha_by_frame`` is one mean-alpha value per frame. A stinger ramps in
    from transparent, holds fully opaque for a while, then breaks away, so the
    cut belongs in the middle of that opaque span -- the moment the picture is
    most thoroughly hidden. If covered spans are disjoint, choose a covered
    frame nearest the midpoint of their outer endpoints, not a gap between them.
    A sweep that never fully covers (no alpha channel, or a subtle overlay)
    falls back to its own midpoint. That still cuts under the busiest part of
    the animation, which is the best available guess.
    """
    if not alpha_by_frame:
        return 0
    covered = [index for index, alpha in enumerate(alpha_by_frame) if alpha >= threshold]
    if not covered:
        logger.warning(
            "the brand sweep never fully covers the frame (peak alpha %d); "
            "cutting at its midpoint instead",
            max(alpha_by_frame),
        )
        return len(alpha_by_frame) // 2
    midpoint = (covered[0] + covered[-1]) // 2
    return min(covered, key=lambda index: abs(index - midpoint))
