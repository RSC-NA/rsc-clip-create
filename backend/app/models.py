from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from backend.app.database import Base


def uuid_value() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    discord_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discord_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    latest_sign_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sessions: Mapped[list["SessionRecord"]] = relationship(back_populates="user")
    movies: Mapped[list["Movie"]] = relationship(back_populates="user")
    clips: Mapped[list["Clip"]] = relationship(back_populates="user")
    oauth_accounts: Mapped[list["OAuthAccount"]] = relationship(back_populates="user")
    brand_sweeps: Mapped[list["BrandSweep"]] = relationship(back_populates="user")


class OAuthAccount(TimestampMixin, Base):
    __tablename__ = "oauth_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_account_id: Mapped[str] = mapped_column(String(128), index=True)
    provider_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship(back_populates="oauth_accounts")


class SessionRecord(TimestampMixin, Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_digest: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class Clip(TimestampMixin, Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="ready")
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    team: Mapped[str | None] = mapped_column(String(255), nullable=True)
    player: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extension: Mapped[str | None] = mapped_column(String(16), nullable=True)

    user: Mapped[User] = relationship(back_populates="clips")


class BrandSweep(TimestampMixin, Base):
    """The stinger played over every cut between clips.

    One per user, and optional: an account with no row of its own renders with
    the sweep bundled in the image. This replaced a brand logo -- a still image
    composited into a generated transition card -- which replaced a per-team
    logo library before that.
    """

    __tablename__ = "brand_sweeps"
    __table_args__ = (UniqueConstraint("user_id", name="uq_brand_sweeps_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    user: Mapped[User] = relationship(back_populates="brand_sweeps")


class Movie(TimestampMixin, Base):
    __tablename__ = "movies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="draft")
    latest_video_id: Mapped[str | None] = mapped_column(
        ForeignKey("videos.id"), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="movies")
    slots: Mapped[list["MovieSlot"]] = relationship(
        back_populates="movie", cascade="all, delete-orphan"
    )
    render_jobs: Mapped[list["RenderJob"]] = relationship(
        back_populates="movie", cascade="all, delete-orphan"
    )


class MovieSlot(TimestampMixin, Base):
    __tablename__ = "movie_slots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    movie_id: Mapped[str] = mapped_column(ForeignKey("movies.id"), index=True)
    clip_id: Mapped[str | None] = mapped_column(ForeignKey("clips.id"), nullable=True)
    sort_index: Mapped[int] = mapped_column(Integer)
    overlay_text: Mapped[str] = mapped_column(Text, default="")

    movie: Mapped[Movie] = relationship(back_populates="slots")


class RenderJob(TimestampMixin, Base):
    __tablename__ = "render_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    movie_id: Mapped[str] = mapped_column(ForeignKey("movies.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    final_status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_clip_level_failures: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted_clip_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_clip_count: Mapped[int] = mapped_column(Integer, default=0)
    latest_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("render_job_attempts.id"), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    movie: Mapped[Movie] = relationship(back_populates="render_jobs")
    attempts: Mapped[list["RenderJobAttempt"]] = relationship(
        back_populates="render_job",
        cascade="all, delete-orphan",
        foreign_keys="RenderJobAttempt.render_job_id",
    )


class RenderJobAttempt(TimestampMixin, Base):
    __tablename__ = "render_job_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    render_job_id: Mapped[str] = mapped_column(ForeignKey("render_jobs.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    provider: Mapped[str] = mapped_column(String(32), default="ffmpeg")
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    failure_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider_response_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider_debug_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    render_job: Mapped[RenderJob] = relationship(
        back_populates="attempts", foreign_keys=[render_job_id]
    )
    clip_results: Mapped[list["RenderJobClipResult"]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class RenderJobClipResult(TimestampMixin, Base):
    __tablename__ = "render_job_clip_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    render_job_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("render_job_attempts.id"), index=True
    )
    # SET NULL, not RESTRICT: a clip result is a render diagnostic and must
    # outlive the timeline edit that removes its slot. Without this, removing a
    # clip from a movie that has been rendered once raises IntegrityError.
    movie_slot_id: Mapped[str | None] = mapped_column(
        ForeignKey("movie_slots.id", ondelete="SET NULL"), nullable=True
    )
    clip_id: Mapped[str | None] = mapped_column(
        ForeignKey("clips.id"), nullable=True, index=True
    )
    clip_order_index: Mapped[int] = mapped_column(Integer)
    submitted_clip_path: Mapped[str] = mapped_column(Text)
    provider_clip_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    failure_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_reason_code: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    reason_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    technical_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    clip_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider_response_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    attempt: Mapped[RenderJobAttempt] = relationship(back_populates="clip_results")
    clip: Mapped[Clip | None] = relationship()


class Video(TimestampMixin, Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_value)
    movie_id: Mapped[str] = mapped_column(ForeignKey("movies.id"), index=True)
    source_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("render_job_attempts.id"), nullable=True
    )
    storage_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    generation_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_asset_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
