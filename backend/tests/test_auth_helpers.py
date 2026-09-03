from backend.app.routers.auth import _safe_next_path


def test_safe_next_path_allows_internal_paths():
    assert _safe_next_path("/movies") == "/movies"


def test_safe_next_path_rejects_external_urls():
    assert _safe_next_path("https://evil.example/steal") == "/movies"
