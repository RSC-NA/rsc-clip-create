"""Write render artifacts a human can actually look at.

Numeric assertions catch drift in the arithmetic. They cannot catch a label in
the wrong place, a logo that failed to composite, or a sting that came out the
wrong colour -- so the sample run also extracts frames and indexes them.
"""

from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path

OUTPUT_ROOT = Path("test-output")


def reset_output(name: str) -> Path:
    directory = OUTPUT_ROOT / name
    if directory.exists():
        shutil.rmtree(directory)
    (directory / "frames").mkdir(parents=True, exist_ok=True)
    return directory


def extract_frame(video: Path, at_seconds: float, destination: Path) -> Path | None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{at_seconds:.3f}", "-i", str(video),
            "-frames:v", "1", str(destination),
        ],
        capture_output=True,
        text=True,
    )
    return destination if result.returncode == 0 and destination.exists() else None


def frame_plan(clip_frames: list[int], hold_frames: int, fps: int) -> list[tuple[str, float]]:
    """Midpoints of each clip and each sting, in output-timeline seconds.

    Mirrors the renderer's own accounting: each boundary adds `hold` frames, so
    clip k starts at sum(previous clips) + k*hold.
    """
    marks: list[tuple[str, float]] = []
    running = 0
    for index, frames in enumerate(clip_frames):
        marks.append((f"clip-{index}", (running + frames / 2) / fps))
        running += frames
        if index < len(clip_frames) - 1 and hold_frames:
            marks.append((f"sting-{index}", (running + hold_frames / 2) / fps))
            running += hold_frames
    return marks


def write_report(
    directory: Path,
    *,
    title: str,
    sources: list[Path],
    plan: dict,
    measured: dict,
    frames: list[tuple[str, Path]],
) -> Path:
    rows = "".join(
        f"<tr><th scope='row'>{html.escape(str(key))}</th>"
        f"<td>{html.escape(str(plan.get(key, '')))}</td>"
        f"<td>{html.escape(str(measured.get(key, '')))}</td></tr>"
        for key in sorted(set(plan) | set(measured))
    )
    tiles = "".join(
        f"<figure><img src='frames/{html.escape(path.name)}' alt='{html.escape(label)}'>"
        f"<figcaption>{html.escape(label)}</figcaption></figure>"
        for label, path in frames
    )
    listed = "".join(f"<li>{html.escape(p.name)}</li>" for p in sources)

    report = directory / "report.html"
    report.write_text(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:2rem;background:#f3efe7;color:#211d16}}
 table{{border-collapse:collapse;margin:1rem 0}}
 th,td{{border:1px solid #cbbfa8;padding:.4rem .8rem;text-align:left}}
 th[scope=row]{{background:#eee5d5}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1rem}}
 figure{{margin:0}} img{{width:100%;border:1px solid #cbbfa8;border-radius:6px}}
 figcaption{{font-size:.8rem;color:#62584a;padding-top:.25rem}}
 video{{max-width:720px;width:100%}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<h2>Source clips</h2><ul>{listed}</ul>
<h2>Plan vs measured</h2>
<table><thead><tr><th>Property</th><th>Planned</th><th>Measured</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>Output</h2><video controls src="render.mp4"></video>
<h2>Frames</h2>
<p>One from the middle of each clip (check the label and logo) and one from the
middle of each sting (check the colour and direction).</p>
<div class="grid">{tiles}</div>
</body></html>""",
        encoding="utf-8",
    )
    (directory / "summary.json").write_text(
        json.dumps({"plan": plan, "measured": measured}, indent=2, default=str),
        encoding="utf-8",
    )
    return report
