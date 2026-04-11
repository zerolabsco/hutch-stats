from __future__ import annotations

from dataclasses import dataclass

from srht_contrib.schemas import NormalizedEvent


@dataclass(slots=True)
class BackfillBatchResult:
    events: list[NormalizedEvent]
    cursor_state: dict | None
    complete: bool
