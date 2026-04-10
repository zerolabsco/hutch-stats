from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from srht_contrib.models import ContributionEvent
from srht_contrib.schemas import ContributionCalendarResponse, ContributionDay, ContributionStatsResponse
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
        return ContributionCalendarResponse(actor=actor, from_date=start, to_date=end, days=days)

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
