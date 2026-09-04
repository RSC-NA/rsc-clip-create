import subprocess
import sys

import pytest
from PIL import Image, ImageDraw

from backend.app.filename_parser import ParsedClipName
from backend.app.filename_parser import LABEL_SEPARATOR
from backend.app.services.brand import (
    BUNDLED_FONT,
    DEFAULT_FAMILY,
    MIRROR,
    TRANSITION_FAMILIES,
    create_label_image,
    create_sting_image,
    plan_sting,
    resolve_font,
)


def _render(text: str, font) -> bytes:
    image = Image.new("L", (200, 120), 0)
    ImageDraw.Draw(image).text((10, 10), text, font=font, fill=255)
    return image.tobytes()


NAME = ParsedClipName("Falcons", "Ace Rivera", "Gold", "mp4")


def _sting(movie="m1", index=0, family="sweep"):
    return plan_sting(movie, index, family, sting_frames=36, fps=30, size=(1920, 1080))


def test_same_movie_and_boundary_is_reproducible():
    assert _sting() == _sting()


def test_different_boundaries_and_movies_diverge():
    assert _sting(index=0) != _sting(index=1)
    assert _sting(movie="a") != _sting(movie="b")


def test_seeding_survives_a_different_process_hash_seed():
    """hash() is randomized per process; this catches a regression to it."""
    script = (
        "from backend.app.services.brand import plan_sting;"
        "s=plan_sting('m1',3,'sweep',sting_frames=36,fps=30,size=(1920,1080));"
        "print(s.transition_in, s.axis, s.wedge_count, s.wedge_skew)"
    )
    outputs = set()
    for seed in ("0", "1", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin", "PYTHONPATH": "."},
        )
        outputs.add(result.stdout.strip())
    assert len(outputs) == 1, outputs


def test_lead_colour_strictly_alternates():
    leads = [_sting(index=i).lead for i in range(6)]
    assert leads[0::2] == [leads[0]] * 3
    assert leads[1::2] == [leads[1]] * 3
    assert leads[0] != leads[1]


@pytest.mark.parametrize("family", sorted(TRANSITION_FAMILIES))
def test_every_family_picks_from_its_own_pool(family):
    spec = _sting(family=family)
    assert spec.transition_in in TRANSITION_FAMILIES[family]


def test_unknown_family_falls_back_to_the_default():
    spec = _sting(family="does-not-exist")
    assert spec.family == DEFAULT_FAMILY
    assert spec.transition_in in TRANSITION_FAMILIES[DEFAULT_FAMILY]


def test_exit_transition_mirrors_the_entry():
    spec = _sting()
    assert spec.transition_out == MIRROR.get(spec.transition_in, spec.transition_in)


def test_mirror_table_is_symmetric():
    for left, right in MIRROR.items():
        assert MIRROR[right] == left


def test_pan_canvas_is_larger_along_the_travel_axis():
    for index in range(8):
        spec = _sting(index=index)
        width, height = spec.canvas
        if spec.axis == "horizontal":
            assert (width, height) == (3840, 1080)
        elif spec.axis == "vertical":
            assert (width, height) == (1920, 2160)
        else:
            assert (width, height) == (3840, 2160)


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
    absent = _render("\ue000", font)          # private use area: always missing
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


def test_label_is_full_frame(tmp_path):
    out = tmp_path / "label.png"
    create_label_image(NAME, out)
    assert Image.open(out).size == (1920, 1080)


def test_the_label_carries_no_logo(tmp_path):
    """Regression for a deliberate change: a per-team mark used to sit to the
    left of the text. The brand logo lives in the sting centre now, so the
    label must not accept or draw one."""
    import inspect

    params = inspect.signature(create_label_image).parameters
    assert "logo_path" not in params
    assert "logo_max_width" not in params


# --------------------------------------------------------------------------
# The brand logo, centred in every sting
# --------------------------------------------------------------------------
# Magenta, deliberately: the sting art is blue, orange and white, so a white
# test mark is indistinguishable from the wedges' leading edges.
MARK_COLOUR = (255, 0, 255)


def _brand_mark(tmp_path, size=(400, 400)):
    path = tmp_path / "brand.png"
    Image.new("RGBA", size, MARK_COLOUR + (255,)).save(path)
    return path


def _sting_spec(index=0):
    """Distinct from _sting above, which the transition-planning tests use."""
    return plan_sting("m1", index, "sweep", sting_frames=36, fps=30, size=(1920, 1080))


def test_the_sting_logo_lands_in_the_centre_of_the_canvas(tmp_path):
    """The pan window's midpoint is the canvas centre, so a mark centred on the
    canvas reads dead centre at the peak of the sweep."""
    spec = _sting_spec()
    out = tmp_path / "sting.png"
    create_sting_image(spec, out, logo_path=_brand_mark(tmp_path))

    image = Image.open(out).convert("RGBA")
    width, height = image.size
    # The mark is opaque white; the wedges behind it are not.
    centre = image.getpixel((width // 2, height // 2))
    assert centre[:3] == MARK_COLOUR, f"expected the mark at the centre, got {centre}"


def test_a_sting_without_a_logo_still_renders(tmp_path):
    spec = _sting_spec()
    out = tmp_path / "plain.png"
    create_sting_image(spec, out, logo_path=None)
    assert Image.open(out).size == spec.canvas


def test_an_unreadable_sting_logo_degrades_to_a_plain_sting(tmp_path):
    """A broken logo must never fail a render."""
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    spec = _sting_spec()

    plain, with_broken = tmp_path / "a.png", tmp_path / "b.png"
    create_sting_image(spec, plain, logo_path=None)
    create_sting_image(spec, with_broken, logo_path=broken)

    assert Image.open(with_broken).tobytes() == Image.open(plain).tobytes()


def test_a_missing_sting_logo_path_is_ignored(tmp_path):
    spec = _sting_spec()
    out = tmp_path / "out.png"
    create_sting_image(spec, out, logo_path=tmp_path / "nope.png")
    assert out.is_file()


def test_the_sting_logo_is_sized_against_the_frame_not_the_canvas(tmp_path):
    """Otherwise a vertical sweep, whose canvas is twice as tall, would carry a
    mark of a different apparent size than a horizontal one."""
    mark = _brand_mark(tmp_path)
    widths = []
    for index in range(6):
        spec = _sting_spec(index)
        out = tmp_path / f"s{index}.png"
        create_sting_image(spec, out, logo_path=mark, logo_scale=0.25)
        # Measure the mark by scanning the canvas centre row for its colour.
        image = Image.open(out).convert("RGBA")
        row = spec.canvas[1] // 2
        span = [
            x for x in range(spec.canvas[0])
            if image.getpixel((x, row))[:3] == MARK_COLOUR
        ]
        widths.append(max(span) - min(span) if span else 0)

    expected = round(min(1920, 1080) * 0.25)
    assert all(abs(w - expected) <= 8 for w in widths), widths


def test_font_resolution_is_host_independent_and_legible():
    font = resolve_font(42)
    assert font.size == 42
