import subprocess
import sys

import pytest
from PIL import Image

from backend.app.filename_parser import ParsedClipName
from backend.app.services.brand import (
    DEFAULT_FAMILY,
    MIRROR,
    TRANSITION_FAMILIES,
    create_label_image,
    plan_sting,
    resolve_font,
)


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


def test_label_is_full_frame_and_widens_for_a_logo(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 200), (255, 0, 0, 255)).save(logo)

    plain, with_logo = tmp_path / "plain.png", tmp_path / "logo-label.png"
    create_label_image(NAME, plain)
    create_label_image(NAME, with_logo, logo_path=logo)

    assert Image.open(plain).size == (1920, 1080)
    assert Image.open(with_logo).size == (1920, 1080)

    def plate_width(path):
        alpha = Image.open(path).split()[-1]
        return alpha.getbbox()[2] - alpha.getbbox()[0]

    assert plate_width(with_logo) > plate_width(plain)


def test_unreadable_logo_falls_back_to_text_only(tmp_path):
    """A bad logo must never fail a render."""
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"this is not an image")

    plain, broken_label = tmp_path / "plain.png", tmp_path / "broken-label.png"
    create_label_image(NAME, plain)
    create_label_image(NAME, broken_label, logo_path=broken)

    assert Image.open(broken_label).tobytes() == Image.open(plain).tobytes()


def test_missing_logo_path_is_ignored(tmp_path):
    out = tmp_path / "out.png"
    create_label_image(NAME, out, logo_path=tmp_path / "nope.png")
    assert out.is_file()


def test_font_resolution_is_host_independent_and_legible():
    font = resolve_font(42)
    assert font.size == 42
