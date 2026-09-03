from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_EXTENSIONS = frozenset({"mp4", "mov", "webm", "m4v", "avi", "mkv"})
FILENAME_RE = re.compile(r"^(?P<team>[^-.]+)-(?P<player>[^-.]+)-(?P<tier>[^-.]+)\.(?P<extension>[A-Za-z0-9]+)$")


@dataclass(frozen=True)
class ParsedClipName:
    team: str
    player: str
    tier: str
    extension: str

    @property
    def label(self) -> str:
        return f"{self.team}\n{self.player}\n{self.tier}"


def parse_clip_filename(filename: str, allowed_extensions: set[str] | None = None) -> ParsedClipName:
    name = Path(filename or "").name
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
    return parsed


def normalize_team(team: str | None) -> str:
    """Canonical form used to match a clip's team token to a stored logo."""
    return (team or "").strip().lower()
