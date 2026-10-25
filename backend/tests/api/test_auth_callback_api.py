from sqlalchemy import func, select

from backend.app.config import get_settings
from backend.app.models import SessionRecord
from backend.app.routers import auth as auth_router


def _set_cookie_headers(response, name):
    return [
        c for c in response.headers.get_list("set-cookie") if c.startswith(f"{name}=")
    ]


def test_discord_callback_cancelled_consent_redirects_to_login(
    anon_client, db_session, require_auth, monkeypatch
):
    settings = get_settings()
    calls = []

    async def _fake_exchange(code):  # pragma: no cover - must not be called
        calls.append(code)
        raise AssertionError("exchange_discord_code must not be called without a code")

    monkeypatch.setattr(auth_router, "exchange_discord_code", _fake_exchange)

    anon_client.cookies.set(settings.oauth_state_cookie_name, "abc")
    anon_client.cookies.set(settings.oauth_next_cookie_name, "/movies")
    response = anon_client.get(
        "/api/v1/auth/discord/callback?error=access_denied&state=abc",
        follow_redirects=False,
    )

    assert response.status_code == 307, response.text
    assert response.headers["location"] == f"{settings.frontend_public_url}/login"

    for name in (settings.oauth_state_cookie_name, settings.oauth_next_cookie_name):
        matching = _set_cookie_headers(response, name)
        assert matching, f"{name} cookie not deleted"
        assert any("max-age=0" in c.lower() for c in matching)
        assert any("path=/" in c.lower() for c in matching)

    assert calls == []
    assert db_session.scalar(select(func.count()).select_from(SessionRecord)) == 0


def test_discord_callback_without_code_or_state_redirects_to_login(
    anon_client, db_session, require_auth, monkeypatch
):
    settings = get_settings()

    async def _fake_exchange(code):  # pragma: no cover - must not be called
        raise AssertionError("exchange_discord_code must not be called without a code")

    monkeypatch.setattr(auth_router, "exchange_discord_code", _fake_exchange)

    response = anon_client.get(
        "/api/v1/auth/discord/callback?error=access_denied", follow_redirects=False
    )

    assert response.status_code == 307, response.text
    assert response.headers["location"] == f"{settings.frontend_public_url}/login"
    assert db_session.scalar(select(func.count()).select_from(SessionRecord)) == 0


def test_discord_callback_with_code_and_wrong_state_still_rejected(anon_client, require_auth):
    settings = get_settings()
    anon_client.cookies.set(settings.oauth_state_cookie_name, "right")
    response = anon_client.get(
        "/api/v1/auth/discord/callback?code=x&state=wrong", follow_redirects=False
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid OAuth state."


def test_discord_callback_with_valid_code_still_signs_in(
    anon_client, db_session, require_auth, monkeypatch
):
    settings = get_settings()
    calls = []

    async def _fake_exchange(code):
        calls.append(code)
        return {"id": "discord-123", "username": "tester", "email": "t@example.invalid"}

    monkeypatch.setattr(auth_router, "exchange_discord_code", _fake_exchange)

    anon_client.cookies.set(settings.oauth_state_cookie_name, "good")
    anon_client.cookies.set(settings.oauth_next_cookie_name, "/clips")
    response = anon_client.get(
        "/api/v1/auth/discord/callback?code=abc&state=good", follow_redirects=False
    )

    assert response.status_code == 307, response.text
    assert response.headers["location"] == f"{settings.frontend_public_url}/clips"
    assert calls == ["abc"]
    assert db_session.scalar(select(func.count()).select_from(SessionRecord)) == 1
    assert _set_cookie_headers(response, settings.session_cookie_name)
    for name in (settings.oauth_state_cookie_name, settings.oauth_next_cookie_name):
        matching = _set_cookie_headers(response, name)
        assert any("max-age=0" in c.lower() for c in matching)
