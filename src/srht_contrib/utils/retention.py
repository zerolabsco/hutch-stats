from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from srht_contrib.models import ContributionEvent


RETENTION_DAYS = 365


def prune_contribution_events(db: Session, *, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(tz=UTC)) - timedelta(days=RETENTION_DAYS)
    result = db.execute(delete(ContributionEvent).where(ContributionEvent.occurred_at < cutoff))
    return int(result.rowcount or 0)
