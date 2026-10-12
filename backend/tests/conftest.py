"""Shared test fixtures.

Two things here are load-bearing and were arrived at by hitting the failure:

1. ``PRAGMA foreign_keys=ON``. SQLite leaves FK enforcement off by default, so
   without this the suite cannot catch constraint bugs that production MySQL
   rejects -- which is exactly how a 500 on removing a clip from a rendered
   movie went unnoticed.
2. ``check_same_thread=False`` *and* ``StaticPool``. TestClient runs requests on
   a different thread than the test body, so a plain in-memory SQLite session
   raises "SQLite objects created in a thread can only be used in that same
   thread". StaticPool additionally keeps every connection pointed at the same
   in-memory database.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from backend.app.database import Base, get_db
from backend.app.deps import get_current_user
from backend.app.main import app
from backend.app.models import BrandSweep, Clip, Movie, MovieSlot, User


SAMPLES_DIR = Path(__file__).parent / "fixtures" / "samples"
MEDIA_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"}


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Point every storage write at tmp_path.

    Autouse and unconditional: `settings` is an lru_cached singleton shared by
    storage.py, the routers and the renderer, so without this an upload test
    would write real files into the repo's storage/ directory.
    """
    from backend.app import storage as storage_module
    from backend.app.config import get_settings

    root = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_root", root)
    monkeypatch.setattr(storage_module.settings, "storage_root", root)
    storage_module.ensure_storage_dirs()
    return root


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(engine) -> Generator[Session, None, None]:
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db_session) -> User:
    record = User(discord_user_id=f"user-{uuid.uuid4()}", display_name="Test User")
    db_session.add(record)
    db_session.commit()
    return record


@pytest.fixture
def other_user(db_session) -> User:
    record = User(discord_user_id=f"other-{uuid.uuid4()}", display_name="Other User")
    db_session.add(record)
    db_session.commit()
    return record


@pytest.fixture
def admin_user(db_session) -> User:
    record = User(
        discord_user_id=f"admin-{uuid.uuid4()}", display_name="Admin User", is_admin=True
    )
    db_session.add(record)
    db_session.commit()
    return record


class _IdentityClient(TestClient):
    """A TestClient that binds its own identity on every request.

    dependency_overrides is global to the app, so two client fixtures in one
    test would otherwise fight over it and the last one created would win --
    silently authenticating both as the same user.
    """

    identity = None

    def request(self, *args, **kwargs):
        _ACTIVE_USER["current"] = self.identity
        return super().request(*args, **kwargs)


_ACTIVE_USER: dict[str, object] = {"current": None}


def _resolve_active_user():
    user = _ACTIVE_USER["current"]
    if user is None:  # pragma: no cover - anon_client uses the real dependency
        raise RuntimeError("no active test user")
    return user


def _client_for(db_session, current_user) -> Generator[TestClient, None, None]:
    # No `with` block: entering the context manager would run lifespan, which
    # calls create_all against the real (MySQL) engine.
    app.dependency_overrides[get_db] = lambda: db_session
    if current_user is not None:
        app.dependency_overrides[get_current_user] = _resolve_active_user

    client = _IdentityClient(app, raise_server_exceptions=False)
    client.identity = current_user
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        _ACTIVE_USER["current"] = None


@pytest.fixture
def client(db_session, user):
    yield from _client_for(db_session, user)


@pytest.fixture
def other_client(db_session, other_user):
    yield from _client_for(db_session, other_user)


@pytest.fixture
def admin_client(db_session, admin_user):
    yield from _client_for(db_session, admin_user)


@pytest.fixture
def anon_client(db_session):
    """No get_current_user override, so the real dependency runs.

    Auth settings are pinned by `require_auth` where a test needs them; without
    that, a developer's local AUTH_ENABLED=false would silently pass these.
    """
    yield from _client_for(db_session, None)


@pytest.fixture
def require_auth(monkeypatch):
    """Pin auth on, regardless of the developer's backend/.env."""
    from backend.app import auth as auth_module

    monkeypatch.setattr(auth_module.settings, "auth_enabled", True)
    return auth_module.settings


@pytest.fixture
def make_clip(db_session, user):
    def _make(name="Falcons-Ace-Gold.mp4", *, owner=None, storage_path=None, **kwargs):
        stem = Path(name).stem.split("-")
        owner = owner or user
        clip = Clip(
            user_id=owner.id,
            original_name=name,
            storage_path=storage_path or f"storage/clips/{uuid.uuid4()}-{name}",
            team=kwargs.pop("team", stem[0] if stem else "Team"),
            player=kwargs.pop("player", stem[1] if len(stem) > 1 else "Player"),
            tier=kwargs.pop("tier", stem[2] if len(stem) > 2 else "Tier"),
            extension=kwargs.pop("extension", Path(name).suffix.lstrip(".") or "mp4"),
            duration_seconds=kwargs.pop("duration_seconds", 12.0),
            **kwargs,
        )
        db_session.add(clip)
        db_session.commit()
        return clip

    return _make


@pytest.fixture
def make_movie(db_session, user):
    def _make(title="Test Movie", *, owner=None, clips=(), **kwargs):
        movie = Movie(user_id=(owner or user).id, title=title, **kwargs)
        db_session.add(movie)
        db_session.flush()
        for index, clip in enumerate(clips):
            db_session.add(
                MovieSlot(movie_id=movie.id, clip_id=clip.id, sort_index=index, overlay_text="")
            )
        db_session.commit()
        return movie

    return _make


@pytest.fixture
def make_sweep(db_session, user, tmp_path):
    """An account's own brand sweep -- one per user, played over every cut.

    The bytes are a placeholder, not real video. Tests that need the sweep to
    decode use the bundled one; these only need a row and a file that exists.
    """

    def _make(*, owner=None, name=None):
        path = tmp_path / (name or f"sweep-{uuid.uuid4().hex[:8]}.mov")
        path.write_bytes(b"not really a video")
        sweep = BrandSweep(
            user_id=(owner or user).id,
            original_name=path.name,
            storage_path=str(path),
            duration_seconds=1.5,
        )
        db_session.add(sweep)
        db_session.commit()
        return sweep

    return _make


# ---------------------------------------------------------------------------
# Media fixtures
# ---------------------------------------------------------------------------
def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg and ffprobe are required"
)


@pytest.fixture
def make_media(tmp_path):
    """Generate a small real clip. Cheap enough to call several times per test."""

    def _make(name="Falcons-Ace-Gold.mp4", *, seconds=1.0, fps=30, size="320x240", audio=True):
        destination = tmp_path / name
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=d={seconds}:s={size}:r={fps}",
        ]
        if audio:
            command += ["-f", "lavfi", "-i", f"sine=frequency=440:d={seconds}"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio:
            command += ["-c:a", "aac", "-shortest"]
        command += [str(destination)]
        subprocess.run(command, check=True, capture_output=True)
        return destination

    return _make


@pytest.fixture
def sample_clips():
    """User-supplied footage from backend/tests/fixtures/samples/."""
    if not SAMPLES_DIR.is_dir():
        pytest.skip(f"no samples directory at {SAMPLES_DIR}")
    found = sorted(
        p for p in SAMPLES_DIR.iterdir() if p.suffix.lower() in MEDIA_SUFFIXES and p.is_file()
    )
    if len(found) < 2:
        pytest.skip(
            f"needs at least 2 clips in {SAMPLES_DIR} named team-player-tier.ext "
            f"(found {len(found)})"
        )
    return found
