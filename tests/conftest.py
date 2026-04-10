from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from srht_contrib.config import Settings
from srht_contrib.db import Base, make_engine
from srht_contrib.main import create_app


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        API_KEY="test-api-key",
        ENABLE_SCHEDULER=False,
        SRHT_TOKEN="test-token",
        DATABASE_URL="sqlite://",
        TODO_SRHT_ENDPOINT="https://todo.sr.ht/query",
        GIT_SRHT_ENDPOINT="https://git.sr.ht/query",
        DEFAULT_ACTOR="~ccleberg",
        POLL_INTERVAL_SECONDS=3600,
        GIT_TRACKED_REPOSITORIES=[],
    )


@pytest.fixture()
def db_engine(settings: Settings):
    engine = make_engine(settings)
    Base.metadata.create_all(bind=engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def session_factory(db_engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False, expire_on_commit=False)


@pytest.fixture()
def db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(settings: Settings, db_engine, session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    app = create_app(settings, engine=db_engine, session_factory=session_factory)
    with TestClient(app) as test_client:
        test_client.headers.update({"X-API-Key": settings.api_key})
        yield test_client
