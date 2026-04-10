from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from srht_contrib.models import ContributionEvent, SyncState, TrackedRepository
from srht_contrib.schemas import NormalizedEvent
from srht_contrib.services.git import GitIngestionService
from srht_contrib.services.todo import TodoIngestionService
from srht_contrib.utils.repositories import canonicalize_repository_name


logger = logging.getLogger(__name__)
SYNC_OVERLAP = timedelta(hours=24)


class PollerService:
    def __init__(self, todo_service: TodoIngestionService, git_service: GitIngestionService) -> None:
        self.todo_service = todo_service
        self.git_service = git_service

    def poll_all(self, db: Session, actor: str) -> int:
        inserted = 0
        inserted += self._poll_service(db, actor, self.todo_service.service_name, self.todo_service.fetch_recent_events)
        self._sync_tracked_repositories(db, actor)
        git_repositories = self._tracked_repositories_for_actor(db, actor)
        inserted += self._poll_service(
            db,
            actor,
            self.git_service.service_name,
            lambda actor, since: self.git_service.fetch_recent_events(
                actor=actor,
                since=since,
                repositories=git_repositories,
            ),
        )
        db.commit()
        return inserted

    def _poll_service(self, db: Session, actor: str, service_name: str, fetcher) -> int:
        state = db.scalar(
            select(SyncState).where(SyncState.service == service_name).where(SyncState.actor == actor)
        )
        since = datetime.now(tz=UTC) - timedelta(days=30)
        if state and state.cursor_value:
            since = datetime.fromisoformat(state.cursor_value.replace("Z", "+00:00")).astimezone(UTC) - SYNC_OVERLAP
            logger.info(
                "Using sync cursor for %s actor=%s with overlap; since=%s",
                service_name,
                actor,
                since.isoformat(),
            )

        result = fetcher(actor=actor, since=since)
        inserted = self._insert_events(db, result.events)
        self._upsert_sync_state(db, service_name, actor, result.cursor)
        logger.info("Polled %s for %s: inserted=%s", service_name, actor, inserted)
        return inserted

    @staticmethod
    def _insert_events(db: Session, events: list[NormalizedEvent]) -> int:
        inserted = 0
        for event in events:
            try:
                with db.begin_nested():
                    model = ContributionEvent(**event.model_dump())
                    db.add(model)
                    db.flush()
                    inserted += 1
            except IntegrityError:
                logger.info("Skipping duplicate event %s for service %s", event.external_uid, event.service)
        return inserted

    @staticmethod
    def _upsert_sync_state(db: Session, service: str, actor: str, cursor_value: str) -> None:
        state = db.scalar(select(SyncState).where(SyncState.service == service).where(SyncState.actor == actor))
        now = datetime.now(tz=UTC)
        if state is None:
            db.add(SyncState(service=service, actor=actor, cursor_value=cursor_value, updated_at=now))
            db.flush()
            return

        state.cursor_value = cursor_value
        state.updated_at = now
        db.add(state)
        db.flush()

    def _sync_tracked_repositories(self, db: Session, actor: str) -> None:
        configured = self.git_service.settings.git_tracked_repositories
        for repo_name in configured:
            canonical_repo_name = canonicalize_repository_name(actor, repo_name)
            existing = db.scalar(
                select(TrackedRepository)
                .where(TrackedRepository.service == self.git_service.service_name)
                .where(TrackedRepository.actor == actor)
                .where(TrackedRepository.repo_name == canonical_repo_name)
            )
            if existing is None:
                db.add(
                    TrackedRepository(
                        service=self.git_service.service_name,
                        repo_name=canonical_repo_name,
                        actor=actor,
                    )
                )
        db.flush()

    def _tracked_repositories_for_actor(self, db: Session, actor: str) -> list[str]:
        rows = db.scalars(
            select(TrackedRepository.repo_name)
            .where(TrackedRepository.service == self.git_service.service_name)
            .where(TrackedRepository.actor == actor)
            .order_by(TrackedRepository.repo_name)
        ).all()
        return list(rows)
