from PIL import Image, ImageDraw

from backend.app.filename_parser import LABEL_SEPARATOR, ParsedClipName, parse_clip_filename
from backend.app.services.brand import (
    BUNDLED_FONT,
    BUNDLED_SWEEP,
    COVER_THRESHOLD,
    cover_frame,
    create_label_image,
    resolve_font,
)


def _render(text: str, font) -> bytes:
    image = Image.new("L", (200, 120), 0)
    ImageDraw.Draw(image).text((10, 10), text, font=font, fill=255)
    return image.tobytes()


NAME = ParsedClipName("Falcons", "Ace Rivera", "Gold", "mp4")


# --------------------------------------------------------------------------
# The clip label
# --------------------------------------------------------------------------
def test_label_is_one_line_separated_by_dots():
    assert NAME.label == "Falcons · Ace Rivera · Gold"
    assert "\n" not in NAME.label


def test_the_bundled_font_is_used_by_default():
    """Vendored so every host renders identically, and so the middle-dot
    separator is guaranteed to have a glyph."""
    assert BUNDLED_FONT.is_file()
    assert resolve_font(56).getname()[0] == "Anton"


def test_the_separator_has_a_glyph_in_the_bundled_font():
    """A missing glyph renders as .notdef -- a box between every word."""
    font = resolve_font(56)
    absent = _render("", font)          # private use area: always missing
    separator = _render(LABEL_SEPARATOR.strip(), font)
    assert separator != absent
    assert any(separator)


def test_the_label_has_no_backing_plate(tmp_path):
    """Regression for a deliberate design change: the text used to sit on a
    translucent rounded rectangle. Only the glyphs and their stroke should be
    opaque now, so the alpha channel must be mostly empty."""
    out = tmp_path / "plain.png"
    create_label_image(NAME, out, stroke_width=0)

    alpha = Image.open(out).split()[-1]
    covered = sum(1 for value in alpha.getdata() if value > 0)
    frame = alpha.size[0] * alpha.size[1]
    assert covered / frame < 0.02, "something opaque is still being drawn"

    # The decisive check, and the reason it is a fraction rather than "is any
    # pixel transparent": the old plate had rounded corners, so it too contained
    # a few transparent pixels. Measured, the plate left 0.1% of its bounding box
    # transparent; bare glyphs leave 45%, and glyphs with a stroke 27%.
    box = alpha.getbbox()
    interior = list(alpha.crop(box).getdata())
    transparent = sum(1 for value in interior if value == 0) / len(interior)
    assert transparent > 0.20, (
        f"only {transparent:.1%} of the label's bounding box is transparent; "
        "a backing plate appears to have returned"
    )


def test_the_stroke_adds_contrast_without_a_plate(tmp_path):
    bare, stroked = tmp_path / "bare.png", tmp_path / "stroked.png"
    create_label_image(NAME, bare, stroke_width=0)
    create_label_image(NAME, stroked, stroke_width=3)

    def covered(path):
        return sum(1 for value in Image.open(path).split()[-1].getdata() if value > 0)

    assert covered(stroked) > covered(bare)


def test_long_valid_label_renders_its_final_tier_glyph_inside_the_frame(tmp_path):
    prefix = "W" * 60
    # Both names pass the upload parser and differ only in the last tier glyph.
    long_name = parse_clip_filename(f"{prefix}-{prefix}-{prefix}.mp4")
    changed_name = parse_clip_filename(f"{prefix}-{prefix}-{'W' * 59}I.mp4")
    long_path, changed_path = tmp_path / "long.png", tmp_path / "changed.png"
    create_label_image(long_name, long_path)
    create_label_image(changed_name, changed_path)

    with Image.open(long_path) as rendered, Image.open(changed_path) as changed:
        alpha = rendered.getchannel("A")
        other_alpha = changed.getchannel("A")
        assert rendered.size == changed.size == (1920, 1080)
        assert alpha.getbbox()[2] <= 1912, "the label touches the right frame edge"
        assert other_alpha.getbbox()[2] <= 1912
        assert alpha.tobytes() != other_alpha.tobytes(), (
            "changing the final tier glyph had no effect on the rendered PNG"
        )


def test_short_label_keeps_its_single_line_and_left_anchor(tmp_path):
    out = tmp_path / "short.png"
    create_label_image(NAME, out)
    with Image.open(out) as rendered:
        left, top, _, bottom = rendered.getchannel("A").getbbox()
        _, text_top, _, text_bottom = ImageDraw.Draw(rendered).textbbox(
            (0, 0), NAME.label, font=resolve_font(56), stroke_width=3
        )
        assert 35 <= left <= 45
        assert bottom - top <= text_bottom - text_top


def test_label_is_full_frame(tmp_path):
    out = tmp_path / "label.png"
    create_label_image(NAME, out)
    assert Image.open(out).size == (1920, 1080)


def test_the_label_carries_no_logo(tmp_path):
    """Regression for a deliberate change: a per-team mark used to sit to the
    left of the text, and then a brand logo sat in the middle of a generated
    transition card. Both are gone -- the brand mark is the sweep now -- and
    the label must not accept or draw one."""
    import inspect

    params = inspect.signature(create_label_image).parameters
    assert "logo_path" not in params
    assert "logo_max_width" not in params


def test_font_resolution_is_host_independent_and_legible():
    font = resolve_font(42)
    assert font.size == 42


# --------------------------------------------------------------------------
# Where the sweep hides the cut
# --------------------------------------------------------------------------
def test_the_sweep_is_bundled():
    """A fresh checkout renders the real transition with no setup."""
    assert BUNDLED_SWEEP.is_file()


def test_the_cut_lands_in_the_middle_of_the_covered_span():
    # Transparent, ramping up, opaque for a while, then breaking away.
    alpha = [0, 60, 180, 255, 255, 255, 255, 255, 200, 40, 0]
    assert cover_frame(alpha) == 5


def test_an_off_centre_covered_span_still_cuts_inside_it():
    alpha = [0, 255, 255, 255, 100, 50, 20, 10, 0, 0]
    frame = cover_frame(alpha)
    assert frame == 2
    assert alpha[frame] >= COVER_THRESHOLD


def test_disjoint_opaque_frames_never_put_the_cut_on_transparency():
    disjoint = [255, 0, 0, 0, 255]
    cut = cover_frame(disjoint)
    assert cut in (0, 4)
    assert disjoint[cut] >= COVER_THRESHOLD

    # Keep the midpoint of a continuous covered span, and the fallback when
    # the sweep has no sufficiently opaque frame at all.
    assert cover_frame([0, 255, 255, 255, 0]) == 2
    assert cover_frame([0, 40, 90, 120, 90, 40, 0]) == 3


def test_a_disjoint_span_with_a_transparent_midpoint_chooses_coverage():
    alpha = [0, 255, 255, 0, 0, 255, 0]
    assert alpha[cover_frame(alpha)] >= COVER_THRESHOLD


def test_a_nearly_opaque_frame_counts_as_covered():
    """The threshold is not 255. A replacement sweep with a soft edge or one
    stray transparent pixel scores a point or two below it and must not be
    read as never covering."""
    alpha = [0, 251, 252, 251, 0]
    assert cover_frame(alpha) == 2


def test_a_sweep_that_never_covers_falls_back_to_its_midpoint():
    """A subtle overlay, or a file with no alpha channel at all. Cutting under
    the busiest part of the animation is the best available guess; failing the
    render is not an option."""
    assert cover_frame([0, 40, 90, 120, 90, 40, 0]) == 3


def test_no_frames_at_all_is_not_an_error():
    assert cover_frame([]) == 0
