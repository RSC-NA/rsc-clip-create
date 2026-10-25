"""Runs the Create Movie form clip-name byte-limit frontend test (node:test) from the pytest suite."""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NODE_TEST = REPO_ROOT / "frontend-tests" / "create-movie-form.test.mjs"


def _typescript_env(tmp_path):
    env = dict(os.environ)
    if (REPO_ROOT / "node_modules" / "typescript").is_dir():
        return env
    install = subprocess.run(
        ["npm", "install", "--no-save", "--no-audit", "--no-fund", "--prefix", str(tmp_path), "typescript@5"],
        capture_output=True, text=True,
    )
    assert install.returncode == 0, "could not install typescript:\n" + install.stdout + install.stderr
    env["TYPESCRIPT_MODULE"] = str(tmp_path / "node_modules" / "typescript")
    return env


def test_create_movie_refuses_clip_name_over_218_utf8_bytes(tmp_path):
    result = subprocess.run(
        [
            "node", "--test",
            "--test-name-pattern", "refuses a clip name over 218 UTF-8 bytes",
            str(NODE_TEST),
        ],
        cwd=REPO_ROOT, env=_typescript_env(tmp_path),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_create_movie_form_frontend_suite(tmp_path):
    result = subprocess.run(
        ["node", "--test", str(NODE_TEST)],
        cwd=REPO_ROOT, env=_typescript_env(tmp_path),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
