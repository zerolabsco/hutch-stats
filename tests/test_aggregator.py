from datetime import UTC, datetime, date

from sqlalchemy.orm import Session

from srht_contrib.models import ContributionEvent
from srht_contrib.services.aggregator import ContributionAggregator


def test_aggregator_zero_fills_days(db_session: Session) -> None:
    db_session.add(
        ContributionEvent(
            service="todo",
            event_type="ticket_created",
            actor="~ccleberg",
            repo_name=None,
            resource_id="1",
            external_uid="todo:1:created",
            occurred_at=datetime(2026, 3, 28, 12, 0, tzinfo=UTC),
            weight=1.0,
            raw_payload_json=None,
        )
    )
    db_session.commit()

    calendar = ContributionAggregator().build_calendar(
        db_session,
        "~ccleberg",
        date(2026, 3, 28),
        date(2026, 3, 30),
    )

    assert [day.model_dump() for day in calendar.days] == [
        {"date": date(2026, 3, 28), "count": 1, "score": 1.0},
        {"date": date(2026, 3, 29), "count": 0, "score": 0.0},
        {"date": date(2026, 3, 30), "count": 0, "score": 0.0},
    ]


def test_stats_calculation(db_session: Session) -> None:
    db_session.add_all(
        [
            ContributionEvent(
                service="todo",
                event_type="ticket_created",
                actor="~ccleberg",
                repo_name=None,
                resource_id="1",
                external_uid="todo:1:created",
                occurred_at=datetime(2026, 3, 28, 12, 0, tzinfo=UTC),
                weight=1.0,
                raw_payload_json=None,
            ),
            ContributionEvent(
                service="todo",
                event_type="ticket_comment",
                actor="~ccleberg",
                repo_name=None,
                resource_id="1",
                external_uid="todo:1:comment:1",
                occurred_at=datetime(2026, 3, 29, 12, 0, tzinfo=UTC),
                weight=0.5,
                raw_payload_json=None,
            ),
        ]
    )
    db_session.commit()

    stats = ContributionAggregator().build_stats(
        db_session,
        "~ccleberg",
        date(2026, 3, 28),
        date(2026, 3, 30),
    )

    assert stats.total_events == 2
    assert stats.total_score == 1.5
    assert stats.active_days == 2
    assert stats.longest_streak == 2
    assert stats.current_streak == 0
