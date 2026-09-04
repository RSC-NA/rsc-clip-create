"""Brand artwork for rendered movies: clip labels and transition stings.

Everything here is pure Pillow and pure functions. No ffmpeg, no database, no
filesystem beyond reading a logo and writing a PNG -- which makes the creative
decisions unit-testable without rendering a frame of video.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, TypeVar

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from backend.app.filename_parser import ParsedClipName


logger = logging.getLogger(__name__)

T = TypeVar("T")

# Transition pools, keyed by the style a movie selects. Every name is a real
# xfade transition in ffmpeg 8.x.
TRANSITION_FAMILIES: dict[str, tuple[str, ...]] = {
    "sweep": (
        "wipeleft", "wiperight", "wipeup", "wipedown",
        "slideleft", "slideright", "slideup", "slidedown",
        "coverleft", "coverright", "coverup", "coverdown",
        "revealleft", "revealright", "revealup", "revealdown",
    ),
    "slice": (
        "hlslice", "hrslice", "vuslice", "vdslice",
        "diagtl", "diagtr", "diagbl", "diagbr",
        "wipetl", "wipetr", "wipebl", "wipebr",
    ),
    "dissolve": (
        "dissolve", "pixelize", "fadegrays", "fadefast", "fadeslow",
        "smoothleft", "smoothright", "smoothup", "smoothdown", "distance",
    ),
    "pop": (
        "circleopen", "circleclose", "circlecrop", "rectcrop", "zoomin",
        "squeezeh", "squeezev", "radial",
        "vertopen", "vertclose", "horzopen", "horzclose",
    ),
    "storm": (
        "hlwind", "hrwind", "vuwind", "vdwind", "hblur",
        "fadeblack", "fadewhite", "dissolve",
    ),
    "classic": ("fade",),
}
DEFAULT_FAMILY = "sweep"

# Leaving a sting by the mirror of the transition that entered it reads as one
# continuous move through the brand card rather than two unrelated wipes.
_MIRROR_PAIRS = [
    ("wipeleft", "wiperight"), ("wipeup", "wipedown"),
    ("slideleft", "slideright"), ("slideup", "slidedown"),
    ("coverleft", "coverright"), ("coverup", "coverdown"),
    ("revealleft", "revealright"), ("revealup", "revealdown"),
    ("hlslice", "hrslice"), ("vuslice", "vdslice"),
    ("hlwind", "hrwind"), ("vuwind", "vdwind"),
    ("diagtl", "diagbr"), ("diagtr", "diagbl"),
    ("wipetl", "wipebr"), ("wipetr", "wipebl"),
    ("circleopen", "circleclose"), ("vertopen", "vertclose"),
    ("horzopen", "horzclose"), ("smoothleft", "smoothright"),
    ("smoothup", "smoothdown"), ("squeezeh", "squeezev"),
]
MIRROR: dict[str, str] = {}
for _a, _b in _MIRROR_PAIRS:
    MIRROR[_a] = _b
    MIRROR[_b] = _a

_HORIZONTAL = re.compile(r"(left|right|hl|hr|horz|squeezeh)")
_VERTICAL = re.compile(r"(up|down|vu|vd|vert|squeezev)")
_DIAGONAL = re.compile(r"(diag|tl|tr|bl|br)")


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
def _seed(movie_id: str, index: int, salt: str) -> int:
    """Stable across processes and Python versions.

    Deliberately not ``hash()`` (randomized per process by PYTHONHASHSEED) and
    not ``random.seed()`` (mutates global state in a long-lived worker).
    """
    digest = hashlib.sha256(f"{movie_id}:{index}:{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _pick(movie_id: str, index: int, salt: str, pool: Sequence[T]) -> T:
    return pool[_seed(movie_id, index, salt) % len(pool)]


def _pick_int(movie_id: str, index: int, salt: str, low: int, high: int) -> int:
    return low + _seed(movie_id, index, salt) % (high - low + 1)


def parse_hex_color(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"Expected a 6-digit hex color, got {value!r}")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), alpha)


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
def _load_logo(logo_path: Path | None, max_width: int, target_height: int):
    if logo_path is None:
        return None
    try:
        with Image.open(logo_path) as raw:
            logo = ImageOps.exif_transpose(raw).convert("RGBA")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        # A bad logo must never fail a render.
        logger.warning("ignoring unreadable logo %s: %s", logo_path, exc)
        return None

    height = max(48, min(target_height, 220))
    width = max(1, round(logo.width * height / logo.height))
    if width > max_width:
        width = max_width
        height = max(1, round(logo.height * max_width / logo.width))
    return logo.resize((width, height), Image.LANCZOS)


def _load_sting_logo(
    logo_path: Path | None, frame: tuple[int, int], scale: float
) -> Image.Image | None:
    """Load and size the brand mark for the centre of a sting.

    Sized against the FRAME, not the oversized pan canvas, so the mark occupies
    the same proportion of the visible picture whichever way the sweep travels.
    A missing or unreadable file yields a plain sting rather than an error.
    """
    if logo_path is None:
        return None
    try:
        with Image.open(logo_path) as raw:
            logo = ImageOps.exif_transpose(raw).convert("RGBA")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        logger.warning("ignoring unreadable sting logo %s: %s", logo_path, exc)
        return None

    frame_width, frame_height = frame
    target = max(1, round(min(frame_width, frame_height) * scale))
    if logo.width >= logo.height:
        size = (target, max(1, round(logo.height * target / logo.width)))
    else:
        size = (max(1, round(logo.width * target / logo.height)), target)
    return logo.resize(size, Image.LANCZOS)


def create_label_image(
    name: ParsedClipName,
    destination: Path,
    *,
    font_size: int = 56,
    font_path: Path | None = None,
    stroke_width: int = 3,
    canvas: tuple[int, int] = (1920, 1080),
) -> None:
    """Full-frame RGBA overlay: one line of text, bottom left, no backing plate.

    Text only. The brand logo lives in the centre of each transition sting now,
    not on the clip; this used to composite a per-team mark to the left of the
    text.

    The text is drawn with a dark stroke rather than sitting on a translucent
    box. Without either, white type disappears over pale footage. Set
    ``stroke_width=0`` for bare glyphs.
    """
    width, height = canvas
    image = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = resolve_font(font_size, font_path)

    text = name.label
    left, top, right, bottom = draw.textbbox(
        (0, 0), text, font=font, stroke_width=stroke_width
    )
    text_height = bottom - top

    margin_left, margin_bottom = 40, 44
    text_y = height - margin_bottom - text_height - top

    draw.text(
        (margin_left, text_y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, 190),
    )
    image.save(destination, format="PNG")


# --------------------------------------------------------------------------
# Transition stings
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class StingSpec:
    """A fully-decided transition. Derived only from (movie_id, boundary_index)."""

    boundary_index: int
    family: str
    transition_in: str
    transition_out: str
    axis: str                       # "horizontal" | "vertical" | "diagonal"
    frame: tuple[int, int]
    canvas: tuple[int, int]
    pan_x_expr: str
    pan_y_expr: str
    lead: tuple[int, int, int, int]
    trail: tuple[int, int, int, int]
    wedge_count: int
    wedge_skew: int

    def as_metadata(self) -> dict:
        return {
            "boundary": self.boundary_index,
            "family": self.family,
            "in": self.transition_in,
            "out": self.transition_out,
            "axis": self.axis,
            "lead": "blue" if self.lead[2] > self.lead[0] else "orange",
        }


def _axis_for(transition: str, movie_id: str, index: int) -> str:
    if _HORIZONTAL.search(transition):
        return "horizontal"
    if _VERTICAL.search(transition):
        return "vertical"
    if _DIAGONAL.search(transition):
        return "diagonal"
    return _pick(movie_id, index, "axis", ("horizontal", "vertical", "diagonal"))


def plan_sting(
    movie_id: str,
    boundary_index: int,
    family: str,
    *,
    sting_frames: int,
    fps: int,
    size: tuple[int, int],
    blue: str = "#0B5CD5",
    orange: str = "#FF7A18",
) -> StingSpec:
    """Decide everything about one transition, reproducibly.

    Seeded by (movie_id, boundary_index) so a re-render of the same movie makes
    identical creative choices, while adjacent boundaries differ.
    """
    pool = TRANSITION_FAMILIES.get(family) or TRANSITION_FAMILIES[DEFAULT_FAMILY]
    transition_in = _pick(movie_id, boundary_index, "transition", pool)
    # Leave by the mirror so motion carries through the card.
    transition_out = MIRROR.get(transition_in, transition_in)

    axis = _axis_for(transition_in, movie_id, boundary_index)
    width, height = size
    canvas = {
        "horizontal": (width * 2, height),
        "vertical": (width, height * 2),
        "diagonal": (width * 2, height * 2),
    }[axis]

    # Strict alternation, not a hash: hashing the colour clumps three blues in a
    # row often enough to look like a bug rather than a rhythm.
    blue_rgba, orange_rgba = parse_hex_color(blue), parse_hex_color(orange)
    lead, trail = (
        (blue_rgba, orange_rgba) if boundary_index % 2 == 0 else (orange_rgba, blue_rgba)
    )

    duration = sting_frames / fps
    forward = _seed(movie_id, boundary_index, "direction") % 2 == 0
    ramp = f"t/{duration:.6f}" if forward else f"(1-t/{duration:.6f})"
    pan_x = f"(iw-ow)*{ramp}" if axis in ("horizontal", "diagonal") else "0"
    pan_y = f"(ih-oh)*{ramp}" if axis in ("vertical", "diagonal") else "0"

    return StingSpec(
        boundary_index=boundary_index,
        family=family if family in TRANSITION_FAMILIES else DEFAULT_FAMILY,
        transition_in=transition_in,
        transition_out=transition_out,
        axis=axis,
        frame=size,
        canvas=canvas,
        pan_x_expr=pan_x,
        pan_y_expr=pan_y,
        lead=lead,
        trail=trail,
        wedge_count=_pick_int(movie_id, boundary_index, "wedges", 4, 9),
        wedge_skew=_pick_int(movie_id, boundary_index, "skew", 120, 320),
    )


def create_sting_image(
    spec: StingSpec,
    destination: Path,
    *,
    logo_path: Path | None = None,
    logo_scale: float = 0.34,
) -> None:
    """Render the brand card that ffmpeg pans across during a transition.

    The canvas is deliberately larger than the frame along the pan axis; ffmpeg
    crops a moving window out of it, which is what turns a static image into a
    sweep for the cost of one PNG.
    """
    # Wedges are drawn as vertical stripes and then transposed for vertical
    # pans, so the bands always run ACROSS the direction of travel. Stripes
    # parallel to the pan barely appear to move.
    transpose = spec.axis == "vertical"
    width, height = (spec.canvas[1], spec.canvas[0]) if transpose else spec.canvas

    image = Image.new("RGBA", (width, height), spec.trail)
    draw = ImageDraw.Draw(image)

    edge = (255, 255, 255, 210)
    band = width / (spec.wedge_count + 1)
    skew = spec.wedge_skew
    for index in range(spec.wedge_count + 2):
        x0 = index * band - skew
        draw.polygon(
            [
                (x0, 0),
                (x0 + band * 0.55, 0),
                (x0 + band * 0.55 + skew, height),
                (x0 + skew, height),
            ],
            fill=spec.lead,
        )
        draw.line([(x0 + skew, height), (x0, 0)], fill=edge, width=6)

    if transpose:
        image = image.transpose(Image.TRANSPOSE)
        draw = ImageDraw.Draw(image)
    width, height = image.size

    # Centred on the canvas, not the frame: the window pans across this image,
    # and its midpoint is the canvas centre, so the mark reads dead centre at
    # the peak of the sweep.
    mark = _load_sting_logo(logo_path, spec.frame, logo_scale)
    if mark is not None:
        image.alpha_composite(
            mark, dest=((width - mark.width) // 2, (height - mark.height) // 2)
        )

    image.save(destination, format="PNG")
