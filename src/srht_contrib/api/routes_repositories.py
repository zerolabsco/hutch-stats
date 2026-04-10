from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from srht_contrib.api.dependencies import get_actor_identity_resolver, get_db, require_api_key
from srht_contrib.models import TrackedRepository
from srht_contrib.schemas import (
    TrackedRepositoryCreateRequest,
    TrackedRepositoryResponse,
    TrackedRepositoryUpdateRequest,
)
from srht_contrib.utils.identity import ActorIdentityResolver
from srht_contrib.utils.repositories import canonicalize_repository_name

router = APIRouter(prefix="/api/repositories", tags=["repositories"], dependencies=[Depends(require_api_key)])


def _to_response(repository: TrackedRepository) -> TrackedRepositoryResponse:
    return TrackedRepositoryResponse(
        id=repository.id,
        service=repository.service,
        actor=repository.actor,
        repo_name=repository.repo_name,
    )


def _get_repository_or_404(db: Session, repository_id: int) -> TrackedRepository:
    repository = db.scalar(select(TrackedRepository).where(TrackedRepository.id == repository_id))
    if repository is None:
        raise HTTPException(status_code=404, detail="Tracked repository not found.")
    return repository


@router.get("", response_model=list[TrackedRepositoryResponse])
def list_tracked_repositories(
    actor: str | None = Query(default=None),
    db: Session = Depends(get_db),
    actor_identity_resolver: ActorIdentityResolver = Depends(get_actor_identity_resolver),
) -> list[TrackedRepositoryResponse]:
    stmt = select(TrackedRepository).where(TrackedRepository.service == "git").order_by(TrackedRepository.repo_name)
    if actor:
        canonical_actor = actor_identity_resolver.canonicalize(actor, db=db)
        stmt = stmt.where(TrackedRepository.actor == canonical_actor)

    repositories = db.scalars(stmt).all()
    return [_to_response(repository) for repository in repositories]


@router.get("/{repository_id}", response_model=TrackedRepositoryResponse)
def get_tracked_repository(
    repository_id: int,
    db: Session = Depends(get_db),
) -> TrackedRepositoryResponse:
    return _to_response(_get_repository_or_404(db, repository_id))


@router.post("", response_model=TrackedRepositoryResponse, status_code=status.HTTP_201_CREATED)
def create_tracked_repository(
    payload: TrackedRepositoryCreateRequest,
    db: Session = Depends(get_db),
    actor_identity_resolver: ActorIdentityResolver = Depends(get_actor_identity_resolver),
) -> TrackedRepositoryResponse:
    canonical_actor = actor_identity_resolver.canonicalize(payload.actor, db=db)
    repository = TrackedRepository(
        service="git",
        actor=canonical_actor,
        repo_name=canonicalize_repository_name(canonical_actor, payload.repo_name),
    )
    db.add(repository)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tracked repository already exists.") from exc

    db.refresh(repository)
    return _to_response(repository)


@router.patch("/{repository_id}", response_model=TrackedRepositoryResponse)
def update_tracked_repository(
    repository_id: int,
    payload: TrackedRepositoryUpdateRequest,
    db: Session = Depends(get_db),
    actor_identity_resolver: ActorIdentityResolver = Depends(get_actor_identity_resolver),
) -> TrackedRepositoryResponse:
    repository = _get_repository_or_404(db, repository_id)

    if payload.actor is not None:
        repository.actor = actor_identity_resolver.canonicalize(payload.actor, db=db)

    if payload.repo_name is not None:
        repository.repo_name = canonicalize_repository_name(repository.actor, payload.repo_name)

    db.add(repository)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tracked repository already exists.") from exc

    db.refresh(repository)
    return _to_response(repository)


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tracked_repository(
    repository_id: int,
    db: Session = Depends(get_db),
) -> Response:
    repository = _get_repository_or_404(db, repository_id)
    db.delete(repository)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
