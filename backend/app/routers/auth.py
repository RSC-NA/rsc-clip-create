from __future__ import annotations

import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from backend.app.auth import (
    build_discord_authorize_url,
    clear_session,
    create_session,
    exchange_discord_code,
    get_current_user_from_request,
    upsert_discord_user,
)
from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.schemas import SessionResponse


router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


def _safe_next_path(candidate: str | None) -> str:
    if not candidate:
        return "/movies"

    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return "/movies"

    return candidate if candidate.startswith("/") else "/movies"


@router.get("/discord/login")
async def discord_login(next: str = "/movies"):
    if not settings.auth_enabled:
        return RedirectResponse(f"{settings.frontend_public_url}{_safe_next_path(next)}")
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(build_discord_authorize_url(state, next))
    response.set_cookie(
        settings.oauth_state_cookie_name,
        state,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=600,
        path="/",
    )
    response.set_cookie(
        settings.oauth_next_cookie_name,
        _safe_next_path(next),
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=600,
        path="/",
    )
    return response


@router.get("/discord/callback")
async def discord_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    if not code:
        # Discord sends ?error=access_denied (and no code) when the user
        # cancels consent. Send them back to the frontend instead of a 422.
        response = RedirectResponse(f"{settings.frontend_public_url}/login")
        response.delete_cookie(settings.oauth_state_cookie_name, path="/")
        response.delete_cookie(settings.oauth_next_cookie_name, path="/")
        return response

    expected_state = request.cookies.get(settings.oauth_state_cookie_name)
    if not expected_state or expected_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    profile = await exchange_discord_code(code)
    user = upsert_discord_user(db, profile)
    next_path = _safe_next_path(request.cookies.get(settings.oauth_next_cookie_name))
    response = RedirectResponse(f"{settings.frontend_public_url}{next_path}")
    create_session(response, db, user)
    response.delete_cookie(settings.oauth_state_cookie_name, path="/")
    response.delete_cookie(settings.oauth_next_cookie_name, path="/")
    return response


@router.get("/session", response_model=SessionResponse | None)
def session(request: Request, db: Session = Depends(get_db)):
    try:
        user = get_current_user_from_request(request, db)
    except HTTPException:
        return None

    return SessionResponse(
        id=user.id,
        displayName=user.display_name,
        email=user.email,
        discordUsername=user.discord_username,
        avatarUrl=user.avatar_url,
        isAdmin=user.is_admin,
        authEnabled=settings.auth_enabled,
    )


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    response = Response(status_code=204)
    clear_session(request, response, db)
    response.delete_cookie(settings.oauth_state_cookie_name, path="/")
    response.delete_cookie(settings.oauth_next_cookie_name, path="/")
    return response
