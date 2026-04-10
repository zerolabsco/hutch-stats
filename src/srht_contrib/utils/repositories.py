from __future__ import annotations

from fastapi import HTTPException, status


def canonicalize_repository_name(actor: str, repo_name: str) -> str:
    normalized_actor = actor.strip()
    normalized_repo_name = repo_name.strip()

    if not normalized_actor:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Actor must not be blank.")
    if not normalized_repo_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Repository name must not be blank.")

    if "/" in normalized_repo_name:
        owner, name = normalized_repo_name.split("/", 1)
        owner = owner.strip()
        name = name.strip()
        if not owner or not name or "/" in name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Repository name must be `name` or `~owner/name`.",
            )
        canonical_owner = owner if owner.startswith("~") else f"~{owner}"
        return f"{canonical_owner}/{name}"

    if "/" in normalized_actor:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Actor must be a canonical sr.ht user.")

    canonical_actor = normalized_actor if normalized_actor.startswith("~") else f"~{normalized_actor}"
    return f"{canonical_actor}/{normalized_repo_name}"
