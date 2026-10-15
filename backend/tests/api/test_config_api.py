"""The /config contract exists so the browser and the API cannot disagree
about filenames. These tests are the thing that keeps that promise honest."""

import json
import re
import shutil
import subprocess

import pytest

from backend.app.filename_parser import CLIENT_FILENAME_PATTERN, parse_clip_filename


VALID = "Falcons-Ace-Gold.mp4"
INVALID = ["a-b-c-d.mp4", "bad.mp4", "a-b.mp4", "-b-c.mp4", "sub/Falcons-Ace-Gold.mp4"]


def test_config_is_served(client):
    body = client.get("/api/v1/config").json()
    assert set(body) == {
        "filenamePattern",
        "supportedExtensions",
        "minClipCount",
        "maxClipCount",
        "maxClipSizeBytes",
        "maxUploadBytes",
        "maxClipDurationSeconds",
        "supportedSweepFormats",
        "maxBrandSweepBytes",
    }
    assert "mp4" in body["supportedExtensions"]


def test_served_pattern_has_no_python_only_syntax(client):
    """Regression: the endpoint served FILENAME_RE.pattern, whose (?P<name>)
    named groups are Python-only. `new RegExp` throws 'Invalid group' on them,
    which crashed /movies/new on render."""
    pattern = client.get("/api/v1/config").json()["filenamePattern"]
    assert "(?P<" not in pattern


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_served_pattern_compiles_and_agrees_with_the_parser_in_javascript(client):
    """Asserting on the absence of '(?P<' is a proxy. This runs the real engine."""
    pattern = client.get("/api/v1/config").json()["filenamePattern"]
    cases = [VALID, *INVALID]
    script = (
        "const [p, names] = [process.argv[1], JSON.parse(process.argv[2])];"
        "const re = new RegExp(p);"
        "console.log(JSON.stringify(names.map((n) => re.test(n))));"
    )
    result = subprocess.run(
        ["node", "-e", script, pattern, json.dumps(cases)],
        capture_output=True, text=True, check=True,
    )
    js_results = json.loads(result.stdout)

    python_results = []
    for name in cases:
        try:
            parse_clip_filename(name)
            python_results.append(True)
        except ValueError:
            python_results.append(False)

    assert js_results == python_results, dict(zip(cases, zip(js_results, python_results)))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_served_filename_preflight_rejects_whitespace_only_segments(client):
    """Run the /config rule in the engine used by the create form before upload."""
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    pattern = response.json()["filenamePattern"]
    names = [
        "   -Ace-Gold.mp4",
        "Falcons-   -Gold.mp4",
        "Falcons-Ace-   .mp4",
        VALID,
        " Falcons - Ace - Gold .mp4",  # whitespace around real labels is valid
    ]
    script = (
        "const re = new RegExp(process.argv[1]);"
        "const names = JSON.parse(process.argv[2]);"
        "console.log(JSON.stringify(names.map(name => re.test(name))));"
    )
    result = subprocess.run(
        ["node", "-e", script, pattern, json.dumps(names)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == [False, False, False, True, True]


def test_served_pattern_enforces_segment_length_limit_like_the_parser(client):
    """The upload parser rejects stripped segments over 80 characters; the
    served preflight must reject them too, or the browser uploads a whole batch
    only to get a 400."""
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    pattern = response.json()["filenamePattern"]
    cases = {
        "A" * 81 + "-Ace-Gold.mp4": False,
        "Falcons-" + "P" * 81 + "-Gold.mp4": False,
        "Falcons-Ace-" + "T" * 81 + ".mp4": False,
        "A" * 80 + "-Ace-Gold.mp4": True,
        " " + "A" * 80 + " -Ace-Gold.mp4": True,
        "   -Ace-Gold.mp4": False,
    }
    names = list(cases)
    expected = [cases[n] for n in names]

    python_results = []
    for name in names:
        try:
            parse_clip_filename(name)
            python_results.append(True)
        except ValueError:
            python_results.append(False)
    assert python_results == expected

    if shutil.which("node") is not None:
        script = (
            "const re = new RegExp(process.argv[1]);"
            "const names = JSON.parse(process.argv[2]);"
            "console.log(JSON.stringify(names.map(name => re.test(name))));"
        )
        result = subprocess.run(
            ["node", "-e", script, pattern, json.dumps(names)],
            capture_output=True, text=True, check=True,
        )
        served_results = json.loads(result.stdout)
    else:
        compiled = re.compile(pattern)
        served_results = [bool(compiled.search(n)) for n in names]

    assert served_results == expected, dict(zip(names, zip(served_results, expected)))


def test_direct_upload_preserves_whitespace_segment_error(client):
    filename = "   -Ace-Gold.mp4"
    response = client.post(
        "/api/v1/clips",
        files=[("files", (filename, b"not a video", "video/mp4"))],
    )
    assert response.status_code == 400, response.text
    assert "Team, player, and tier must not be empty." in response.json()["detail"]


def test_client_pattern_matches_the_parser_in_python(client):
    compiled = re.compile(CLIENT_FILENAME_PATTERN)
    assert compiled.fullmatch(VALID)
    for name in INVALID:
        assert not compiled.fullmatch(name), name


def test_limits_match_settings(client):
    from backend.app.config import get_settings

    settings = get_settings()
    body = client.get("/api/v1/config").json()
    assert body["minClipCount"] == settings.min_clip_count
    assert body["maxClipCount"] == settings.max_clip_count
    assert body["maxClipSizeBytes"] == settings.max_clip_size_bytes
