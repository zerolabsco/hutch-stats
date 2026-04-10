from __future__ import annotations

import logging
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class SourceHutClientError(RuntimeError):
    """Raised when a SourceHut GraphQL request fails."""


class SourceHutGraphQLClient:
    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        timeout: float = 15.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_retries = max_retries
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(headers=headers, timeout=timeout, transport=transport)

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        attempts = self.max_retries + 1

        for attempt in range(1, attempts + 1):
            try:
                response = self._client.post(self.endpoint, json=payload)
                response.raise_for_status()
                body = response.json()
            except httpx.HTTPStatusError as exc:
                response_text = exc.response.text[:500]
                logger.warning(
                    "SourceHut HTTP failure from %s on attempt %s/%s: %s %s",
                    self.endpoint,
                    attempt,
                    attempts,
                    exc.response.status_code,
                    response_text,
                )
                if exc.response.status_code >= 500 and attempt < attempts:
                    continue
                raise SourceHutClientError(
                    f"HTTP error from SourceHut: {exc.response.status_code} {response_text}".strip()
                ) from exc
            except httpx.HTTPError as exc:
                logger.warning(
                    "SourceHut network failure from %s on attempt %s/%s",
                    self.endpoint,
                    attempt,
                    attempts,
                )
                if attempt < attempts:
                    continue
                raise SourceHutClientError("Network error while contacting SourceHut") from exc

            if "errors" in body:
                raise SourceHutClientError(f"GraphQL errors returned by SourceHut: {body['errors']}")
            return body.get("data", {})

        raise SourceHutClientError("SourceHut request exhausted retries")

    def close(self) -> None:
        self._client.close()
