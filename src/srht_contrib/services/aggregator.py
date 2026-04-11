from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from srht_contrib.models import ContributionEvent, TrackedActor
from srht_contrib.schemas import (
    ContributionCalendarResponse,
    ContributionDay,
    ContributionIndexMetadata,
    ContributionStatsResponse,
)
from srht_contrib.utils.dates import date_range, date_to_utc_bounds


@dataclass(slots=True)
class DailyAggregate:
    date: date
    count: int
    score: float


class ContributionAggregator:
    def build_calendar(self, db: Session, actor: str, start: date, end: date) -> ContributionCalendarResponse:
        aggregates = self._query_daily_aggregates(db, actor, start, end)
        by_day = {row.date: row for row in aggregates}
        days = [
            ContributionDay(
                date=day,
                count=by_day.get(day, DailyAggregate(date=day, count=0, score=0.0)).count,
                score=by_day.get(day, DailyAggregate(date=day, count=0, score=0.0)).score,
            )
            for day in date_range(start, end)
        ]
        metadata = self._index_metadata(db, actor)
        return ContributionCalendarResponse(
            actor=actor,
            from_date=start,
            to_date=end,
            days=days,
            **metadata.model_dump(),
        )

    def build_stats(self, db: Session, actor: str, start: date, end: date) -> ContributionStatsResponse:
        calendar = self.build_calendar(db, actor, start, end)
        active_days = [day for day in calendar.days if day.count > 0]
        streaks = self._streak_lengths(calendar.days)
        current_streak = self._current_streak(calendar.days)

        return ContributionStatsResponse(
            actor=actor,
            from_date=start,
            to_date=end,
            total_events=sum(day.count for day in calendar.days),
            total_score=round(sum(day.score for day in calendar.days), 2),
            active_days=len(active_days),
            longest_streak=max(streaks, default=0),
            current_streak=current_streak,
            is_indexed=calendar.is_indexed,
            last_polled_at=calendar.last_polled_at,
            indexing_state=calendar.indexing_state,
            is_recent_window_backfilled=calendar.is_recent_window_backfilled,
            recent_backfill_state=calendar.recent_backfill_state,
            recent_backfill_completed_at=calendar.recent_backfill_completed_at,
            is_backfilled=calendar.is_backfilled,
            backfill_state=calendar.backfill_state,
            backfill_completed_at=calendar.backfill_completed_at,
        )

    def _index_metadata(self, db: Session, actor: str) -> ContributionIndexMetadata:
        tracked_actor = db.scalar(select(TrackedActor).where(TrackedActor.actor == actor))
        has_indexed_events = db.scalar(select(ContributionEvent.id).where(ContributionEvent.actor == actor).limit(1)) is not None
        last_poll_status = tracked_actor.last_poll_status if tracked_actor is not None else None
        is_indexed = has_indexed_events or (tracked_actor is not None and tracked_actor.last_polled_at is not None)

        if last_poll_status == "error":
            indexing_state = "error"
        elif is_indexed:
            indexing_state = "indexed"
        else:
            indexing_state = "pending"

        return ContributionIndexMetadata(
            is_indexed=is_indexed,
            last_polled_at=tracked_actor.last_polled_at if tracked_actor is not None else None,
            indexing_state=indexing_state,
            is_recent_window_backfilled=(tracked_actor.recent_backfill_status == "completed") if tracked_actor is not None else False,
            recent_backfill_state=(tracked_actor.recent_backfill_status if tracked_actor is not None else "pending"),
            recent_backfill_completed_at=tracked_actor.recent_backfill_completed_at if tracked_actor is not None else None,
            is_backfilled=(tracked_actor.backfill_status == "completed") if tracked_actor is not None else False,
            backfill_state=(tracked_actor.backfill_status if tracked_actor is not None else "pending"),
            backfill_completed_at=tracked_actor.backfill_completed_at if tracked_actor is not None else None,
        )

    def _query_daily_aggregates(self, db: Session, actor: str, start: date, end: date) -> list[DailyAggregate]:
        start_dt, _ = date_to_utc_bounds(start)
        _, end_dt = date_to_utc_bounds(end)

        stmt = (
            select(
                func.date(ContributionEvent.occurred_at).label("day"),
                func.count(ContributionEvent.id).label("count"),
                func.coalesce(func.sum(ContributionEvent.weight), 0.0).label("score"),
            )
            .where(ContributionEvent.actor == actor)
            .where(ContributionEvent.occurred_at >= start_dt)
            .where(ContributionEvent.occurred_at <= end_dt)
            .group_by(func.date(ContributionEvent.occurred_at))
            .order_by(func.date(ContributionEvent.occurred_at))
        )
        rows = db.execute(stmt).all()
        return [
            DailyAggregate(
                date=date.fromisoformat(str(row.day)),
                count=int(row.count),
                score=round(float(row.score), 2),
            )
            for row in rows
        ]

    @staticmethod
    def _streak_lengths(days: list[ContributionDay]) -> list[int]:
        streaks: list[int] = []
        current = 0
        for day in days:
            if day.count > 0:
                current += 1
            elif current > 0:
                streaks.append(current)
                current = 0
        if current > 0:
            streaks.append(current)
        return streaks

    @staticmethod
    def _current_streak(days: list[ContributionDay]) -> int:
        streak = 0
        for day in reversed(days):
            if day.count > 0:
                streak += 1
            else:
                break
        return streak
