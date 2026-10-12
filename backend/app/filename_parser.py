from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_EXTENSIONS = frozenset({"mp4", "mov", "webm", "m4v", "avi", "mkv"})

# One definition, two spellings. The segment class excludes path separators so a
# name like "sub/Falcons-Ace-Gold.mp4" is rejected outright rather than being
# silently basenamed -- that collapse discarded the uuid prefix in save_upload
# and let two users write to the same file.
_SEGMENT = r"[^-./\\]+"
_EXTENSION = r"[A-Za-z0-9]+"

# Served to the browser by GET /api/v1/config. Require a non-whitespace
# character within each segment: the upload parser strips segments before
# rejecting empty ones. The lookahead stops at a segment delimiter, while the
# consuming class still permits leading/trailing whitespace. Keep this free of
# Python named groups so it can be compiled by JavaScript's RegExp.
_CLIENT_SEGMENT = rf"(?=[^-./\\]*[^\s\-./\\]){_SEGMENT}"
CLIENT_FILENAME_PATTERN = rf"^{_CLIENT_SEGMENT}-{_CLIENT_SEGMENT}-{_CLIENT_SEGMENT}\.{_EXTENSION}$"

FILENAME_RE = re.compile(
    rf"^(?P<team>{_SEGMENT})-(?P<player>{_SEGMENT})-(?P<tier>{_SEGMENT})"
    rf"\.(?P<extension>{_EXTENSION})$"
)

# U+00B7 MIDDLE DOT. Verified present in the bundled Anton face; a font
# without it would render .notdef boxes between every word.
LABEL_SEPARATOR = " · "

MAX_SEGMENT_LENGTH = 80


@dataclass(frozen=True)
class ParsedClipName:
    team: str
    player: str
    tier: str
    extension: str

    @property
    def label(self) -> str:
        """One line, middle-dot separated: "Falcons · Ace · Gold"."""
        return LABEL_SEPARATOR.join((self.team, self.player, self.tier))


def parse_clip_filename(filename: str, allowed_extensions: set[str] | None = None) -> ParsedClipName:
    name = filename or ""
    if "/" in name or "\\" in name:
        raise ValueError("Filename must not contain a path separator.")
    if name != Path(name).name:
        raise ValueError("Filename must not contain a path.")

    match = FILENAME_RE.fullmatch(name)
    if not match:
        raise ValueError("Filename must use the format team-player-tier.fileext.")

    parsed = ParsedClipName(
        team=match.group("team").strip(),
        player=match.group("player").strip(),
        tier=match.group("tier").strip(),
        extension=match.group("extension").lower(),
    )
    extensions = allowed_extensions or set(DEFAULT_EXTENSIONS)
    if parsed.extension not in extensions:
        raise ValueError(f"Unsupported video extension .{parsed.extension}.")
    if not all((parsed.team, parsed.player, parsed.tier)):
        raise ValueError("Team, player, and tier must not be empty.")
    # Each segment lands in a VARCHAR(255); bound it here so an over-long name
    # is a 400 rather than a MySQL DataError 500 at commit time.
    for label, value in (("Team", parsed.team), ("Player", parsed.player), ("Tier", parsed.tier)):
        if len(value) > MAX_SEGMENT_LENGTH:
            raise ValueError(f"{label} must be {MAX_SEGMENT_LENGTH} characters or fewer.")
    return parsed

