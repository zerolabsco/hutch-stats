from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from srht_contrib.config import Settings
from srht_contrib.db import get_db_session
from srht_contrib.jobs.poller import PollerService
from srht_contrib.utils.identity import ActorIdentityResolver


def get_db(request: Request):
    yield from get_db_session(request)


def get_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Settings not configured.")
    return settings


def get_actor_identity_resolver(request: Request) -> ActorIdentityResolver:
    resolver = getattr(request.app.state, "actor_identity_resolver", None)
    if resolver is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Actor identity resolver not configured.",
        )
    return resolver


def get_poller(request: Request) -> PollerService:
    poller = getattr(request.app.state, "poller", None)
    if poller is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Poller not configured.")
    return poller


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")
