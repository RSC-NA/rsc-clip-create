"""README setup says `cp backend/.env.example backend/.env`. The copied file
must only set variables Settings reads, and must not override code defaults
with stale values."""

from pathlib import Path

from backend.app.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _keys(text: str) -> list[str]:
    keys = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())
    return keys


def test_env_example_matches_settings(monkeypatch):
    text = ENV_EXAMPLE.read_text()

    # A fresh install loading the copied example draws labels at the code default.
    monkeypatch.delenv("RENDER_FONT_SIZE", raising=False)
    settings = Settings(_env_file=str(ENV_EXAMPLE))
    assert settings.render_font_size == 56
    assert "RENDER_FONT_SIZE=42" not in text

    # Every variable in the example is a real Settings field (extra="ignore"
    # would otherwise drop it silently).
    fields = {name.upper() for name in Settings.model_fields}
    unknown = [key for key in _keys(text) if key.upper() not in fields]
    assert unknown == [], f"variables Settings ignores: {unknown}"

    # The RENDER_FONT_PATH comment must describe the bundled Anton fallback.
    assert "Pillow's bundled font" not in text
    assert "bundled Anton font" in text
