import pytest

from backend.app.filename_parser import parse_clip_filename


def test_parse_clip_filename():
    parsed = parse_clip_filename("Falcons-Ace-Gold.mp4")
    assert (parsed.team, parsed.player, parsed.tier, parsed.extension) == ("Falcons", "Ace", "Gold", "mp4")


@pytest.mark.parametrize("filename", ["bad.mp4", "a-b.mp4", "a-b-c-d.mp4", "a-b-c.exe", "-b-c.mp4"])
def test_reject_invalid_clip_filename(filename):
    with pytest.raises(ValueError):
        parse_clip_filename(filename)
