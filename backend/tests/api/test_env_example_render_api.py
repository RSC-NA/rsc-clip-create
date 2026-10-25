"""A fresh local install follows the README: `cp backend/.env.example
backend/.env`, then starts the API and worker from the repository root. A
movie rendered on that install must draw its labels at the code default size
(56) with the bundled Anton font -- the example file used to pin 42 and claim
Pillow's font was the fallback."""

import shutil
from pathlib import Path

from PIL import Image

from backend.app.config import Settings
from backend.app.services import render_worker
from backend.app.services.brand import create_label_image


ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
OBSOLETE = (
    "RENDER_TRANSITION_STYLE",
    "RENDER_STING_HOLD_SECONDS",
    "RENDER_STING_BLEND_SECONDS",
    "RENDER_BRAND_BLUE",
    "RENDER_BRAND_ORANGE",
    "RENDER_LOGO_MAX_WIDTH",
    "MAX_LOGO_SIZE_BYTES",
)


def _example_keys() -> list[str]:
    keys = []
    for line in ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.append(line.split("=", 1)[0].strip())
    return keys


def _fresh_install_settings(tmp_path, monkeypatch) -> Settings:
    """Settings exactly as a fresh checkout loads them after the README's cp."""
    checkout = tmp_path / "checkout"
    (checkout / "backend").mkdir(parents=True)
    shutil.copy(ENV_EXAMPLE, checkout / "backend" / ".env")
    # Nothing from the developer's shell may mask what the file says.
    for key in {*_example_keys(), "RENDER_FONT_SIZE", "RENDER_FONT_PATH"}:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(checkout)
    return Settings()


def test_fresh_install_renders_labels_at_default_size_with_anton(
    client, db_session, make_movie, make_clip, tmp_path, monkeypatch
):
    # Every key the example sets must be one Settings reads; extra="ignore"
    # would otherwise drop it without a word.
    fields = {name.upper() for name in Settings.model_fields}
    keys = _example_keys()
    assert [key for key in keys if key.upper() not in fields] == []
    assert not [key for key in keys if key.startswith(OBSOLETE)]

    settings = _fresh_install_settings(tmp_path, monkeypatch)
    for name in ("render_font_size", "render_font_path", "render_label_stroke_width"):
        monkeypatch.setattr(render_worker.settings, name, getattr(settings, name))

    clips = [make_clip(f"Falcons-P{i}-Gold.mp4") for i in range(2)]
    movie = make_movie(clips=clips)
    queued = client.post(f"/api/v1/movies/{movie.id}/render")
    assert queued.status_code == 200, queued.text

    captured = {}
    label_dir = tmp_path / "labels"
    label_dir.mkdir()

    def fake_render(inputs, output, options=None, on_progress=None):
        # Draw the first label the way the renderer does, from the options the
        # worker built out of Settings.
        captured["options"] = options
        create_label_image(
            inputs[0].name,
            label_dir / "rendered.png",
            font_size=options.font_size,
            font_path=options.font_path,
            stroke_width=options.stroke_width,
        )
        captured["name"] = inputs[0].name
        return {"duration": 1.0}

    render_worker.process_next_job(db_session, renderer=fake_render)

    job = client.get(f"/api/v1/jobs/{queued.json()['id']}")
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "succeeded"

    options = captured["options"]
    assert options.font_size == 56
    assert options.font_path is None

    # Unset RENDER_FONT_PATH means the bundled Anton font at 56px, so the
    # label is pixel-identical to one drawn explicitly that way, and differs
    # from the stale 42px the example used to set.
    anton = Path(render_worker.__file__).resolve().parents[1] / "assets" / "fonts" / "Anton-Regular.ttf"
    create_label_image(
        captured["name"], label_dir / "anton56.png",
        font_size=56, font_path=anton, stroke_width=options.stroke_width,
    )
    create_label_image(
        captured["name"], label_dir / "anton42.png",
        font_size=42, font_path=anton, stroke_width=options.stroke_width,
    )
    rendered = Image.open(label_dir / "rendered.png").tobytes()
    assert rendered == Image.open(label_dir / "anton56.png").tobytes()
    assert rendered != Image.open(label_dir / "anton42.png").tobytes()
