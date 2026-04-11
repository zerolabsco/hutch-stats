from __future__ import annotations

import copy
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from srht_contrib.models import ContributionEvent, ServiceBackfillState, SyncState, TrackedActor, TrackedRepository
from srht_contrib.schemas import NormalizedEvent
from srht_contrib.services.git import GitIngestionService
from srht_contrib.services.srht_client import SourceHutClientError
from srht_contrib.services.todo import TodoIngestionService
from srht_contrib.utils.repositories import canonicalize_repository_name


logger = logging.getLogger(__name__)
SYNC_OVERLAP = timedelta(hours=24)


class PollerService:
    def __init__(self, todo_service: TodoIngestionService, git_service: GitIngestionService) -> None:
        self.todo_service = todo_service
        self.git_service = git_service

    def poll_all(self, db: Session, actor: str) -> int:
        self.track_actor_request(db, actor, update_last_requested=False)
        try:
            inserted = self._poll_actor(db, actor)
        except Exception as exc:
            db.rollback()
            self._update_tracked_actor_poll_state(db, actor, status="error", error=str(exc))
            db.commit()
            raise

        self._update_tracked_actor_poll_state(db, actor, status="indexed", error=None)
        inserted += self._run_backfill_batches(db, actor)
        db.commit()
        return inserted

    def poll_tracked_actors(self, db: Session, default_actor: str | None = None) -> dict[str, int]:
        if default_actor:
            self.track_actor_request(db, default_actor, update_last_requested=False)
            db.commit()

        results: dict[str, int] = {}
        actors = db.scalars(
            select(TrackedActor.actor)
            .where(TrackedActor.is_active.is_(True))
            .order_by(TrackedActor.last_requested_at.is_(None), TrackedActor.last_requested_at.desc(), TrackedActor.actor)
        ).all()
        for actor in actors:
            try:
                results[actor] = self.poll_all(db, actor)
            except SourceHutClientError:
                logger.exception("Scheduled poll failed for actor=%s", actor)
            except Exception:
                logger.exception("Unexpected scheduled poll failure for actor=%s", actor)
        return results

    def track_actor_request(self, db: Session, actor: str, *, update_last_requested: bool = True) -> TrackedActor:
        tracked_actor = db.scalar(select(TrackedActor).where(TrackedActor.actor == actor))
        if tracked_actor is None:
            tracked_actor = TrackedActor(actor=actor, is_active=True, backfill_status="pending")
            db.add(tracked_actor)

        tracked_actor.is_active = True
        if update_last_requested:
            tracked_actor.last_requested_at = datetime.now(tz=UTC)
        db.flush()
        return tracked_actor

    def _poll_actor(self, db: Session, actor: str) -> int:
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

    def _update_tracked_actor_poll_state(self, db: Session, actor: str, status: str, error: str | None) -> None:
        tracked_actor = self.track_actor_request(db, actor, update_last_requested=False)
        tracked_actor.last_poll_status = status
        tracked_actor.last_poll_error = error
        if status == "indexed":
            tracked_actor.last_polled_at = datetime.now(tz=UTC)
        db.add(tracked_actor)
        db.flush()

    def _run_backfill_batches(self, db: Session, actor: str) -> int:
        tracked_actor = self.track_actor_request(db, actor, update_last_requested=False)
        if tracked_actor.backfill_status == "completed":
            return 0

        if tracked_actor.backfill_started_at is None:
            tracked_actor.backfill_started_at = datetime.now(tz=UTC)
        tracked_actor.backfill_status = "in_progress"
        tracked_actor.last_backfill_error = None
        db.add(tracked_actor)
        db.flush()
        total_inserted = 0

        services = [
            (self.todo_service.service_name, self.todo_service.fetch_backfill_batch),
            (self.git_service.service_name, self.git_service.fetch_backfill_batch),
        ]
        all_complete = True
        for service_name, fetcher in services:
            state = db.scalar(
                select(ServiceBackfillState)
                .where(ServiceBackfillState.actor == actor)
                .where(ServiceBackfillState.service == service_name)
            )
            if state is None:
                state = ServiceBackfillState(
                    actor=actor,
                    service=service_name,
                    cursor_json=None,
                    status="pending",
                    started_at=None,
                    completed_at=None,
                    last_error=None,
                    updated_at=datetime.now(tz=UTC),
                )
                db.add(state)
                db.flush()

            if state.status == "completed":
                continue

            all_complete = False
            if state.started_at is None:
                state.started_at = datetime.now(tz=UTC)
            state.status = "in_progress"
            state.updated_at = datetime.now(tz=UTC)
            try:
                result = fetcher(actor=actor, cursor_state=state.cursor_json)
                inserted = self._insert_events(db, result.events)
                total_inserted += inserted
                state.cursor_json = copy.deepcopy(result.cursor_state)
                state.last_error = None
                state.updated_at = datetime.now(tz=UTC)
                if result.complete:
                    state.status = "completed"
                    state.completed_at = datetime.now(tz=UTC)
                    logger.info("Backfill complete for service=%s actor=%s inserted=%s", service_name, actor, inserted)
                else:
                    logger.info("Backfill batch complete for service=%s actor=%s inserted=%s", service_name, actor, inserted)
            except Exception as exc:
                state.status = "error"
                state.last_error = str(exc)
                state.updated_at = datetime.now(tz=UTC)
                tracked_actor.backfill_status = "error"
                tracked_actor.last_backfill_error = str(exc)
                db.add(state)
                db.add(tracked_actor)
                db.flush()
                raise

            db.add(state)
            db.flush()

        completed = db.scalars(
            select(ServiceBackfillState.status).where(ServiceBackfillState.actor == actor)
        ).all()
        if completed and all(status == "completed" for status in completed):
            tracked_actor.backfill_status = "completed"
            tracked_actor.backfill_completed_at = datetime.now(tz=UTC)
            tracked_actor.last_backfill_error = None
        elif tracked_actor.backfill_status != "error":
            tracked_actor.backfill_status = "in_progress"
        db.add(tracked_actor)
        db.flush()
        return total_inserted
