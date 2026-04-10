from __future__ import annotations

from collections.abc import Generator

from fastapi import HTTPException, Request, status
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from srht_contrib.config import Settings

Base = declarative_base()


def make_engine(settings: Settings) -> Engine:
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    engine_kwargs = {"future": True, "connect_args": connect_args}
    if settings.database_url in {"sqlite://", "sqlite:///:memory:"}:
        engine_kwargs["poolclass"] = StaticPool
    return create_engine(settings.database_url, **engine_kwargs)


def make_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = make_engine(settings)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def validate_db(bind: Engine) -> None:
    with bind.connect() as connection:
        connection.execute(text("SELECT 1"))


def get_db() -> Generator[Session, None, None]:
    raise RuntimeError("Use get_db(request) dependency injection with a Request parameter.")


def get_session_factory(request: Request) -> sessionmaker[Session]:
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database not configured.")
    return session_factory


def get_db_session(request: Request) -> Generator[Session, None, None]:
    session_factory = get_session_factory(request)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
