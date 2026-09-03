from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.models import OAuthAccount, SessionRecord, User


settings = get_settings()


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def create_session(response: Response, db: Session, user: User) -> None:
    raw_token = secrets.token_urlsafe(48)
    session = SessionRecord(
        user_id=user.id,
        token_digest=_hash_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    db.add(session)
    db.commit()

    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=60 * 60 * 24 * 14,
        path="/",
    )


def clear_session(request: Request, response: Response, db: Session) -> None:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        existing = db.scalar(
            select(SessionRecord).where(
                SessionRecord.token_digest == _hash_token(raw_token)
            )
        )
        if existing:
            db.delete(existing)
            db.commit()

    response.delete_cookie(settings.session_cookie_name, path="/")


def get_current_user_from_request(request: Request, db: Session) -> User:
    if not settings.auth_enabled:
        return get_or_create_bypass_user(db)

    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    session = db.scalar(
        select(SessionRecord).where(SessionRecord.token_digest == _hash_token(raw_token))
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    expires_at = _coerce_utc(session.expires_at)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    user = db.get(User, session.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    return user


def get_or_create_bypass_user(db: Session) -> User:
    """Return a stable local identity for explicitly disabled-auth testing."""
    user = db.scalar(select(User).where(User.discord_user_id == "local-test-user"))
    if user is None:
        user = User(
            discord_user_id="local-test-user",
            display_name=settings.auth_bypass_display_name,
            email="local-test@example.invalid",
            discord_username="local-test-user",
            is_admin=settings.auth_bypass_admin,
            latest_sign_in_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.commit()
    elif user.is_admin != settings.auth_bypass_admin:
        user.is_admin = settings.auth_bypass_admin
        db.commit()
    return user


def require_admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def build_discord_authorize_url(state: str, next_path: str) -> str:
    query = urlencode(
        {
            "client_id": settings.discord_client_id,
            "redirect_uri": settings.discord_redirect_uri,
            "response_type": "code",
            "scope": "identify email",
            "state": state,
            "prompt": "consent",
        }
    )
    return f"https://discord.com/api/oauth2/authorize?{query}"


async def exchange_discord_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_response.raise_for_status()
        token_data = token_response.json()

        user_response = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        user_response.raise_for_status()
        profile = user_response.json()

    return profile


def upsert_discord_user(db: Session, profile: dict) -> User:
    user = db.scalar(select(User).where(User.discord_user_id == profile["id"]))
    username = profile.get("global_name") or profile.get("username") or "Discord User"
    avatar_hash = profile.get("avatar")
    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{profile['id']}/{avatar_hash}.png"
        if avatar_hash
        else None
    )

    if user is None:
        user = User(
            discord_user_id=profile["id"],
            display_name=username,
            email=profile.get("email"),
            discord_username=profile.get("username"),
            avatar_url=avatar_url,
            is_admin=profile["id"] in settings.admin_discord_ids,
            latest_sign_in_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
    else:
        user.display_name = username
        user.email = profile.get("email")
        user.discord_username = profile.get("username")
        user.avatar_url = avatar_url
        user.is_admin = user.is_admin or profile["id"] in settings.admin_discord_ids
        user.latest_sign_in_at = datetime.now(timezone.utc)

    account = db.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == "discord",
            OAuthAccount.provider_account_id == profile["id"],
        )
    )
    if account is None:
        account = OAuthAccount(
            user_id=user.id,
            provider="discord",
            provider_account_id=profile["id"],
            provider_username=profile.get("username"),
        )
        db.add(account)
    else:
        account.provider_username = profile.get("username")

    db.commit()
    db.refresh(user)
    return user
