from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from srht_contrib.db import Base


class ContributionEvent(Base):
    __tablename__ = "contribution_events"
    __table_args__ = (
        UniqueConstraint("service", "external_uid", name="uq_contribution_event_service_uid"),
        Index("ix_contribution_events_actor_occurred_at", "actor", "occurred_at"),
        Index("ix_contribution_events_service_occurred_at", "service", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    raw_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class SyncState(Base):
    __tablename__ = "sync_states"
    __table_args__ = (UniqueConstraint("service", "actor", name="uq_sync_state_service_actor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    cursor_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrackedRepository(Base):
    __tablename__ = "tracked_repositories"
    __table_args__ = (
        UniqueConstraint("service", "actor", "repo_name", name="uq_tracked_repository_service_actor_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)


class ActorAlias(Base):
    __tablename__ = "actor_aliases"
    __table_args__ = (UniqueConstraint("alias", name="uq_actor_alias_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_actor: Mapped[str] = mapped_column(String(255), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)


class TrackedActor(Base):
    __tablename__ = "tracked_actors"
    __table_args__ = (UniqueConstraint("actor", name="uq_tracked_actor_actor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_poll_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    recent_backfill_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    recent_backfill_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recent_backfill_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_recent_backfill_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    backfill_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    backfill_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backfill_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_backfill_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ServiceBackfillState(Base):
    __tablename__ = "service_backfill_states"
    __table_args__ = (UniqueConstraint("actor", "service", "scope", name="uq_service_backfill_state_actor_service_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="full")
    cursor_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
