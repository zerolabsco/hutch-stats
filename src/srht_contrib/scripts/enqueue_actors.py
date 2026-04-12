from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re

from sqlalchemy import select

from srht_contrib.config import Settings
from srht_contrib.db import make_session_factory
from srht_contrib.models import TrackedActor


USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?$")


def _normalize_actor(raw_username: str) -> str | None:
    username = raw_username.strip()
    if not username or username.startswith("#"):
        return None
    if username.startswith("~"):
        username = username[1:]
    if not USERNAME_RE.fullmatch(username):
        return None
    return f"~{username}"


def _iter_usernames(path: Path) -> list[str]:
    usernames: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        actor = _normalize_actor(raw_line)
        if actor is None:
            continue
        if actor in seen:
            continue
        seen.add(actor)
        usernames.append(actor)
    return usernames


def enqueue_actors(username_file: Path, *, stagger_seconds: int = 300, start_at: datetime | None = None) -> int:
    settings = Settings()
    session_factory = make_session_factory(settings)
    usernames = _iter_usernames(username_file)
    queued_at = start_at or datetime.now(tz=UTC)
    inserted = 0

    batch_size = 250
    queued_in_batch = 0

    with session_factory() as db:
        for index, actor in enumerate(usernames):
            next_poll_after = queued_at + timedelta(seconds=index * stagger_seconds)
            tracked_actor = db.scalar(select(TrackedActor).where(TrackedActor.actor == actor))
            if tracked_actor is None:
                tracked_actor = TrackedActor(
                    actor=actor,
                    is_active=True,
                    discovery_state="queued",
                    queued_for_discovery_at=queued_at,
                    next_poll_after=next_poll_after,
                    recent_backfill_status="pending",
                )
                db.add(tracked_actor)
                inserted += 1
            else:
                tracked_actor.is_active = True
                if tracked_actor.queued_for_discovery_at is None:
                    tracked_actor.queued_for_discovery_at = queued_at
                if tracked_actor.last_polled_at is None and tracked_actor.discovery_state != "indexed":
                    tracked_actor.discovery_state = "queued"
                    tracked_actor.next_poll_after = next_poll_after

            queued_in_batch += 1
            if queued_in_batch >= batch_size:
                db.commit()
                queued_in_batch = 0

        if queued_in_batch:
            db.commit()

    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Durably enqueue SourceHut actors without polling them immediately.")
    parser.add_argument(
        "username_file",
        nargs="?",
        default="srht_usernames.txt",
        help="Path to a newline-delimited SourceHut username file.",
    )
    parser.add_argument(
        "--stagger-seconds",
        type=int,
        default=300,
        help="Seconds to space out each actor's first eligible poll time.",
    )
    args = parser.parse_args()

    inserted = enqueue_actors(Path(args.username_file), stagger_seconds=args.stagger_seconds)
    print(f"Enqueued {inserted} new actors from {args.username_file}.")


if __name__ == "__main__":
    main()
