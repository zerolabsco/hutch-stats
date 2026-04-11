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


USER_REPOSITORIES_QUERY = """
query UserRepositories($username: String!, $cursor: Cursor) {
  user(username: $username) {
    repositories(cursor: $cursor) {
      results {
        name
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
    ) -> GitPollResult:
        since_dt = ensure_utc(since or (datetime.now(tz=UTC) - timedelta(days=30)))
        discovered_repositories = repositories or self._repositories_for_actor(actor)
        if not discovered_repositories:
            logger.info("git poll skipped for actor=%s because no repositories were discovered", actor)
            return GitPollResult(events=[], cursor=datetime.now(tz=UTC).isoformat())

        events: list[NormalizedEvent] = []
        for repository in discovered_repositories:
            owner, repo_name = self._split_repository(actor, repository)
            repo_events = self._fetch_repository_commits(actor=actor, owner=owner, repo_name=repo_name, since=since_dt)
            events.extend(repo_events)

        logger.info("git poll complete for actor=%s normalized_events=%s", actor, len(events))
        return GitPollResult(events=events, cursor=datetime.now(tz=UTC).isoformat())

    def _repositories_for_actor(self, actor: str) -> list[str]:
        configured = {
            self._canonical_repository_name(actor, repository)
            for repository in self.settings.git_tracked_repositories
        }
        discovered = set(self._discover_owned_repositories(actor))
        repositories = sorted(configured | discovered)
        logger.info(
            "git repositories selected for actor=%s count=%s configured=%s discovered=%s",
            actor,
            len(repositories),
            len(configured),
            len(discovered),
        )
        return repositories

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
                name = repository.get("name")
                repository_owner = ((repository.get("owner") or {}).get("canonicalName") or actor).strip()
                if not name or not repository_owner:
                    continue
                repositories.append(f"{repository_owner}/{name}")

            if not cursor:
                break

        return repositories

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
