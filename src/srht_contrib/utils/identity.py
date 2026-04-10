from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from srht_contrib.models import ActorAlias


class ActorIdentityResolver:
    def __init__(self, configured_aliases: dict[str, list[str]] | None = None) -> None:
        self.configured_aliases = configured_aliases or {}

    def canonicalize(self, actor: str, db: Session | None = None) -> str:
        normalized = actor.strip()
        if not normalized:
            return normalized

        for canonical, aliases in self.configured_aliases.items():
            if normalized == canonical or normalized in aliases:
                return canonical

        if db is not None:
            alias = db.scalar(select(ActorAlias).where(ActorAlias.alias == normalized))
            if alias is not None:
                return alias.canonical_actor

        return normalized
