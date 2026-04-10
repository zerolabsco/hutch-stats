from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from srht_contrib.config import Settings
from srht_contrib.schemas import NormalizedEvent
from srht_contrib.services.srht_client import SourceHutGraphQLClient
from srht_contrib.utils.dates import ensure_utc, parse_datetime
from srht_contrib.utils.identity import ActorIdentityResolver


logger = logging.getLogger(__name__)


REPOSITORY_LOG_QUERY = """
query RepositoryLog($username: String!, $repoName: String!, $cursor: Cursor) {
  user(username: $username) {
    repository(name: $repoName) {
      name
      owner {
        canonicalName
      }
      log(cursor: $cursor) {
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


@dataclass(slots=True)
class GitPollResult:
    events: list[NormalizedEvent]
    cursor: str


class GitIngestionService:
    """Polls tracked git.sr.ht repositories and normalizes commits for one actor."""

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
    ) -> GitPollResult:
        since_dt = ensure_utc(since or (datetime.now(tz=UTC) - timedelta(days=30)))
        tracked_repositories = repositories or self._tracked_repositories(actor)
        if not tracked_repositories:
            logger.info("git poll skipped for actor=%s because no tracked repositories are configured", actor)
            return GitPollResult(events=[], cursor=datetime.now(tz=UTC).isoformat())

        events: list[NormalizedEvent] = []
        for repository in tracked_repositories:
            owner, repo_name = self._split_repository(actor, repository)
            repo_events = self._fetch_repository_commits(actor=actor, owner=owner, repo_name=repo_name, since=since_dt)
            events.extend(repo_events)

        logger.info("git poll complete for actor=%s normalized_events=%s", actor, len(events))
        return GitPollResult(events=events, cursor=datetime.now(tz=UTC).isoformat())

    def _tracked_repositories(self, actor: str) -> list[str]:
        repositories = self.settings.git_tracked_repositories
        return repositories

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
                {"username": owner, "repoName": repo_name, "cursor": cursor},
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
