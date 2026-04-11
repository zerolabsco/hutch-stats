from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class NormalizedEvent(BaseModel):
    service: str
    event_type: str
    actor: str
    repo_name: str | None = None
    resource_id: str
    external_uid: str
    occurred_at: datetime
    weight: float
    raw_payload_json: dict | None = None


class ContributionDay(BaseModel):
    date: date
    count: int
    score: float


class ContributionIndexMetadata(BaseModel):
    is_indexed: bool
    last_polled_at: datetime | None = None
    indexing_state: Literal["pending", "indexed", "error"]
    is_recent_window_backfilled: bool = False
    recent_backfill_state: Literal["pending", "in_progress", "completed", "error"] = "pending"
    recent_backfill_completed_at: datetime | None = None
    is_backfilled: bool = False
    backfill_state: Literal["pending", "in_progress", "completed", "error"] = "pending"
    backfill_completed_at: datetime | None = None


class ContributionCalendarResponse(ContributionIndexMetadata):
    actor: str
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    days: list[ContributionDay]

    model_config = {"populate_by_name": True}


class ContributionStatsResponse(ContributionIndexMetadata):
    actor: str
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    total_events: int
    total_score: float
    active_days: int
    longest_streak: int
    current_streak: int

    model_config = {"populate_by_name": True}


class HealthResponse(BaseModel):
    status: str


class PollResponse(BaseModel):
    actor: str
    inserted_events: int
    services: list[str]


class TrackedRepositoryCreateRequest(BaseModel):
    actor: str
    repo_name: str


class TrackedRepositoryUpdateRequest(BaseModel):
    actor: str | None = None
    repo_name: str | None = None

    @model_validator(mode="after")
    def validate_any_field_present(self) -> "TrackedRepositoryUpdateRequest":
        if self.actor is None and self.repo_name is None:
            raise ValueError("Provide `actor`, `repo_name`, or both.")
        return self


class TrackedRepositoryResponse(BaseModel):
    id: int
    service: str
    actor: str
    repo_name: str
