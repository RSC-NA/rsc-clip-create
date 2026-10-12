"""Run the create form's handlers against a stubbed browser fetch boundary.

The frontend has no component test runner, so execute the actual validate/submit
handler bodies in Node with the form state and fetch boundary supplied by the test.
"""

import json
import subprocess
from pathlib import Path


def test_browser_create_preflights_title_before_upload():
    source = (Path(__file__).resolve().parents[3] / "app/components/create-movie-form.tsx").read_text()
    handlers = "  function validate(" + source.split("  function validate(", 1)[1].split(
        "  return (\n    <section", 1
    )[0]
    # Remove type-only syntax, keeping the real handler bodies and request order.
    handlers = handlers.replace("selected: File[]", "selected")
    handlers = handlers.replace(" as { id: string }[]", "")
    handlers = handlers.replace(" as { id: string }", "")

    script = """
      const config = {
        filenamePattern: '^[^-]+-[^-]+-[^.]+\\\\.mp4$',
        supportedExtensions: ['mp4'], minClipCount: 1, maxClipCount: 10,
        maxClipSizeBytes: 1000, maxUploadBytes: 10000,
      };
      const filenamePattern = new RegExp(config.filenamePattern);
      const mb = (n) => Math.round(n / 1000000);
      const browserApiPath = (p) => '/api/backend' + p;
      const readApiError = async () => new Error('API rejected request');
      async function exercise(title) {
        const file = new Blob(['clip'], {type: 'video/mp4'});
        file.name = 'Falcons-Ace-Gold.mp4';
        const files = [file];
        const posts = [];
        const router = {push() {}, refresh() {}};
        let error = null;
        let pending;
        const setError = (value) => {error = value};
        const startTransition = (task) => {pending = task()};
        const fetch = async (path, options) => {
          if (options.method === 'POST') posts.push({path, body: options.body});
          return {ok: true, json: async () =>
            path.endsWith('/clips') ? [{id: 'clip-1'}] : {id: 'movie-1'}};
        };
    """ + handlers + """
        submit();
        await pending;
        return {
          posts: posts.map(({path}) => path),
          movieTitle: posts.find(({path}) => path.endsWith('/movies'))?.body
            ? JSON.parse(posts.find(({path}) => path.endsWith('/movies')).body).title
            : null,
          error,
        };
      }
      Promise.all([
        exercise('x'.repeat(255)), exercise('x'.repeat(256)), exercise('🎬'.repeat(255)),
      ]).then(results => console.log(JSON.stringify(results)))
        .catch(error => {console.error(error); process.exitCode = 1});
    """
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    accepted, rejected, unicode_accepted = json.loads(result.stdout.strip())
    assert accepted == {
        "posts": ["/api/backend/clips", "/api/backend/movies"],
        "movieTitle": "x" * 255,
        "error": None,
    }
    assert rejected["posts"] == []
    assert rejected["movieTitle"] is None
    assert "255" in rejected["error"]
    assert unicode_accepted == {
        "posts": ["/api/backend/clips", "/api/backend/movies"],
        "movieTitle": "🎬" * 255,
        "error": None,
    }
