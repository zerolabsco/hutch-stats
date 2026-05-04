from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from srht_contrib.config import Settings
from srht_contrib.models import DiscoveredRepository, TrackedRepository
from srht_contrib.schemas import NormalizedEvent
from srht_contrib.services.srht_client import SourceHutClientError, SourceHutGraphQLClient
from srht_contrib.services.types import BackfillBatchResult
from srht_contrib.utils.dates import ensure_utc, parse_datetime
from srht_contrib.utils.identity import ActorIdentityResolver


logger = logging.getLogger(__name__)


REPOSITORY_LOG_QUERY = """
query RepositoryLog($username: String!, $repoName: String!, $cursor: Cursor, $from: String) {
  user(username: $username) {
    repository(name: $repoName) {
      name
      owner {
        canonicalName
      }
      HEAD {
        name
        target
      }
      log(cursor: $cursor, from: $from) {
        results {
          id
          shortId
          author {
            name
            email
            time
          }
          committer {
            name
            email
            time
          }
          message
        }
        cursor
      }
    }
  }
}
""".strip()


USER_REPOSITORIES_QUERY = """
query UserRepositories($username: String!, $cursor: Cursor) {
  user(username: $username) {
    repositories(cursor: $cursor) {
      results {
        name
        visibility
        owner {
          canonicalName
        }
      }
      cursor
    }
  }
}
""".strip()


@dataclass(slots=True)
class GitPollResult:
    events: list[NormalizedEvent]
    cursor: str


class GitIngestionService:
    """Polls git.sr.ht repositories and normalizes commits for one actor."""

    service_name = "git"

    def __init__(self, client: SourceHutGraphQLClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        self.identity_resolver = ActorIdentityResolver(settings.actor_aliases_json)

    def fetch_recent_events(
        self,
        actor: str,
        since: datetime | None = None,
        repositories: list[str] | None = None,
        db: Session | None = None,
    ) -> GitPollResult:
        since_dt = ensure_utc(since or (datetime.now(tz=UTC) - timedelta(days=30)))
        discovered_repositories = repositories or self._repositories_for_actor(actor, db)
        if not discovered_repositories:
            logger.info("git poll skipped for actor=%s because no repositories were discovered", actor)
            return GitPollResult(events=[], cursor=datetime.now(tz=UTC).isoformat())

        events: list[NormalizedEvent] = []
        for repository in discovered_repositories:
            owner, repo_name = self._split_repository(actor, repository)
            try:
                repo_events = self._fetch_repository_commits(actor=actor, owner=owner, repo_name=repo_name, since=since_dt)
            except SourceHutClientError:
                logger.warning("git poll skipped repository=%s/%s due to GraphQL error", owner, repo_name, exc_info=True)
                continue
            events.extend(repo_events)

        logger.info("git poll complete for actor=%s normalized_events=%s", actor, len(events))
        return GitPollResult(events=events, cursor=datetime.now(tz=UTC).isoformat())

    def _repositories_for_actor(self, actor: str, db: Session | None = None) -> list[str]:
        configured = {
            self._canonical_repository_name(actor, repository)
            for repository in self.settings.git_tracked_repositories
        }
        tracked = set()
        discovered = set()
        now = datetime.now(tz=UTC)

        if db is not None:
            tracked = set(
                db.scalars(
                    select(TrackedRepository.repo_name)
                    .where(TrackedRepository.service == self.service_name)
                    .where(TrackedRepository.actor == actor)
                ).all()
            )
            cutoff = now - timedelta(seconds=self.settings.git_repo_discovery_ttl_seconds)
            discovered = set(
                db.scalars(
                    select(DiscoveredRepository.name)
                    .where(DiscoveredRepository.actor == actor)
                    .where(DiscoveredRepository.discovered_at > cutoff)
                ).all()
            )

        if not discovered:
            discovered = set(self._discover_owned_repositories(actor))
            if db is not None:
                self._refresh_discovered_repositories(db, actor, discovered, discovered_at=now)

        repositories = sorted(configured | tracked | discovered)
        logger.info(
            "git repositories selected for actor=%s count=%s configured=%s tracked=%s discovered=%s",
            actor,
            len(repositories),
            len(configured),
            len(tracked),
            len(discovered),
        )
        return repositories

    def fetch_backfill_batch(self, actor: str, cursor_state: dict | None = None) -> BackfillBatchResult:
        return self._fetch_backfill_batch(actor, cursor_state, since=None)

    def fetch_recent_backfill_batch(
        self,
        actor: str,
        cursor_state: dict | None = None,
        *,
        since: datetime,
    ) -> BackfillBatchResult:
        return self._fetch_backfill_batch(actor, cursor_state, since=since)

    def _fetch_backfill_batch(
        self,
        actor: str,
        cursor_state: dict | None,
        *,
        since: datetime | None,
    ) -> BackfillBatchResult:
        state = {
            "discovery_cursor": None,
            "discovery_complete": False,
            "repository_queue": sorted(
                {
                    self._canonical_repository_name(actor, repository)
                    for repository in self.settings.git_tracked_repositories
                }
            ),
            "current_repository": None,
        }
        if cursor_state:
            state.update(copy.deepcopy(cursor_state))

        if not state["discovery_complete"]:
            data = self.client.execute(
                USER_REPOSITORIES_QUERY,
                {"username": actor.lstrip("~"), "cursor": state["discovery_cursor"]},
            )
            user = data.get("user") or {}
            repositories_page = user.get("repositories") or {}
            results = repositories_page.get("results") or []
            state["discovery_cursor"] = repositories_page.get("cursor")
            state["discovery_complete"] = not bool(state["discovery_cursor"])
            known = set(state["repository_queue"])
            current_repository = state.get("current_repository")
            if current_repository:
                known.add(current_repository["name"])
            for repository in results:
                if not isinstance(repository, dict):
                    continue
                if repository.get("visibility") != "PUBLIC":
                    continue
                name = repository.get("name")
                repository_owner = ((repository.get("owner") or {}).get("canonicalName") or actor).strip()
                if not name or not repository_owner:
                    continue
                canonical_name = f"{repository_owner}/{name}"
                if canonical_name not in known:
                    state["repository_queue"].append(canonical_name)
                    known.add(canonical_name)
            state["repository_queue"] = sorted(state["repository_queue"])
            logger.info(
                "git backfill discovery actor=%s page_count=%s queue=%s next_cursor=%s",
                actor,
                len(results),
                len(state["repository_queue"]),
                bool(state["discovery_cursor"]),
            )
            complete = state["discovery_complete"] and not state["repository_queue"] and not state["current_repository"]
            return BackfillBatchResult(events=[], cursor_state=state, complete=complete)

        if state["current_repository"] is None:
            if not state["repository_queue"]:
                return BackfillBatchResult(events=[], cursor_state=None, complete=True)
            state["current_repository"] = {"name": state["repository_queue"].pop(0), "cursor": None}

        repository_name = state["current_repository"]["name"]
        owner, repo_name = self._split_repository(actor, repository_name)
        data = self.client.execute(
            REPOSITORY_LOG_QUERY,
            {
                "username": owner,
                "repoName": repo_name,
                "cursor": state["current_repository"]["cursor"],
                "from": "HEAD",
            },
        )
        user = data.get("user") or {}
        repository = user.get("repository") or {}
        log_page = repository.get("log") or {}
        commits = log_page.get("results") or []
        next_cursor = log_page.get("cursor")
        events: list[NormalizedEvent] = []
        stop_repository = False
        for commit in commits:
            if not isinstance(commit, dict):
                continue
            commit_time = parse_datetime((commit.get("author") or {}).get("time"))
            if since is not None and commit_time < since:
                stop_repository = True
                break
            normalized = self._normalize_commit(actor=actor, repo_name=repo_name, commit=commit)
            if normalized is not None:
                events.append(normalized)
        logger.info(
            "git backfill actor=%s repository=%s commits=%s next_cursor=%s",
            actor,
            repository_name,
            len(commits),
            bool(next_cursor),
        )
        if next_cursor and not stop_repository:
            state["current_repository"]["cursor"] = next_cursor
        else:
            state["current_repository"] = None

        complete = state["discovery_complete"] and not state["repository_queue"] and not state["current_repository"]
        return BackfillBatchResult(events=events, cursor_state=None if complete else state, complete=complete)

    def _discover_owned_repositories(self, actor: str) -> list[str]:
        owner = actor.lstrip("~")
        repositories: list[str] = []
        cursor: str | None = None

        for _ in range(50):
            data = self.client.execute(
                USER_REPOSITORIES_QUERY,
                {"username": owner, "cursor": cursor},
            )
            user = data.get("user") or {}
            repositories_page = user.get("repositories") or {}
            results = repositories_page.get("results") or []
            cursor = repositories_page.get("cursor")
            logger.info(
                "git repository discovery actor=%s page_count=%s next_cursor=%s",
                actor,
                len(results),
                bool(cursor),
            )

            for repository in results:
                if not isinstance(repository, dict):
                    continue
                if repository.get("visibility") != "PUBLIC":
                    continue
                name = repository.get("name")
                repository_owner = ((repository.get("owner") or {}).get("canonicalName") or actor).strip()
                if not name or not repository_owner:
                    continue
                repo_name = f"{repository_owner}/{name}"
                repositories.append(repo_name)

            if not cursor:
                break

        return repositories

    @staticmethod
    def _refresh_discovered_repositories(
        db: Session,
        actor: str,
        repositories: set[str],
        *,
        discovered_at: datetime,
    ) -> None:
        db.execute(delete(DiscoveredRepository).where(DiscoveredRepository.actor == actor))
        for repository in sorted(repositories):
            db.add(
                DiscoveredRepository(
                    actor=actor,
                    name=repository,
                    discovered_at=discovered_at,
                )
            )
        db.flush()

    @staticmethod
    def _canonical_repository_name(default_actor: str, repository: str) -> str:
        owner, repo_name = GitIngestionService._split_repository(default_actor, repository)
        return f"~{owner}/{repo_name}"

    @staticmethod
    def _split_repository(default_actor: str, repository: str) -> tuple[str, str]:
        if "/" in repository:
            owner, repo_name = repository.split("/", 1)
            canonical_owner = owner if owner.startswith("~") else f"~{owner}"
            return canonical_owner.lstrip("~"), repo_name
        return default_actor.lstrip("~"), repository

    def _fetch_repository_commits(
        self,
        *,
        actor: str,
        owner: str,
        repo_name: str,
        since: datetime,
    ) -> list[NormalizedEvent]:
        events: list[NormalizedEvent] = []
        cursor: str | None = None

        for _ in range(50):
            data = self.client.execute(
                REPOSITORY_LOG_QUERY,
                {"username": owner, "repoName": repo_name, "cursor": cursor, "from": "HEAD"},
            )
            user = data.get("user") or {}
            repository = user.get("repository") or {}
            log_page = repository.get("log") or {}
            commits = log_page.get("results") or []
            cursor = log_page.get("cursor")
            logger.info(
                "git repository=%s/%s commit page count=%s next_cursor=%s",
                owner,
                repo_name,
                len(commits),
                bool(cursor),
            )

            stop_paging = False
            for commit in commits:
                if not isinstance(commit, dict):
                    continue
                commit_time = parse_datetime((commit.get("author") or {}).get("time"))
                if commit_time < since:
                    stop_paging = True
                    logger.info(
                        "git commit %s skipped because commit_time=%s is before since=%s",
                        commit.get("shortId") or commit.get("id"),
                        commit_time.isoformat(),
                        since.isoformat(),
                    )
                    continue

                normalized = self._normalize_commit(actor=actor, repo_name=repo_name, commit=commit)
                if normalized is not None:
                    logger.info(
                        "git commit accepted repo=%s shortId=%s author=%s email=%s",
                        repo_name,
                        commit.get("shortId"),
                        (commit.get("author") or {}).get("name"),
                        (commit.get("author") or {}).get("email"),
                    )
                    events.append(normalized)
                else:
                    logger.info(
                        "git commit skipped repo=%s shortId=%s author=%s email=%s",
                        repo_name,
                        commit.get("shortId"),
                        (commit.get("author") or {}).get("name"),
                        (commit.get("author") or {}).get("email"),
                    )

            if stop_paging or not cursor:
                break

        return events

    def _normalize_commit(
        self,
        *,
        actor: str,
        repo_name: str,
        commit: dict[str, Any],
    ) -> NormalizedEvent | None:
        author = commit.get("author") or {}
        candidate_aliases = [
            actor,
            author.get("email", ""),
            author.get("name", ""),
        ]
        matched_actor = None
        for candidate in candidate_aliases:
            canonical = self.identity_resolver.canonicalize(candidate)
            if canonical == actor:
                matched_actor = canonical
                break

        if matched_actor is None:
            return None

        commit_id = str(commit["id"])
        commit_time = parse_datetime(author["time"])
        return NormalizedEvent(
            service=self.service_name,
            event_type="commit",
            actor=matched_actor,
            repo_name=repo_name,
            resource_id=commit_id,
            external_uid=f"git:{repo_name}:{commit_id}",
            occurred_at=commit_time,
            weight=self.settings.event_weights["commit"],
            raw_payload_json=commit,
        )
