from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from srht_contrib.config import Settings
from srht_contrib.schemas import NormalizedEvent
from srht_contrib.services.srht_client import SourceHutGraphQLClient
from srht_contrib.utils.dates import ensure_utc, parse_datetime


logger = logging.getLogger(__name__)


TODO_ACTIVITY_QUERY = """
query TodoActivity($cursor: Cursor) {
  me {
    canonicalName
  }
  events(cursor: $cursor) {
    results {
      id
      created
      ticket {
        id
        ref
        status
        resolution
        tracker {
          name
        }
      }
      changes {
        __typename
        eventType
        ticket {
          id
        }
        ... on Created {
          author {
            canonicalName
          }
        }
        ... on Comment {
          author {
            canonicalName
          }
        }
        ... on StatusChange {
          editor {
            canonicalName
          }
          oldStatus
          newStatus
          oldResolution
          newResolution
        }
      }
    }
    cursor
  }
}
""".strip()

TODO_TRACKERS_QUERY = """
query TodoTrackers($cursor: Cursor) {
  me {
    canonicalName
    trackers(cursor: $cursor) {
      results {
        id
        rid
        name
      }
      cursor
    }
  }
}
""".strip()

TODO_TRACKER_TICKETS_QUERY = """
query TodoTrackerTickets($trackerRid: ID!, $cursor: Cursor) {
  tracker(rid: $trackerRid) {
    id
    name
    tickets(cursor: $cursor) {
      results {
        id
        ref
        created
        updated
        status
        resolution
        submitter {
          canonicalName
        }
      }
      cursor
    }
  }
}
""".strip()

TODO_TICKET_EVENTS_QUERY = """
query TodoTicketEvents($trackerRid: ID!, $ticketId: Int!, $cursor: Cursor) {
  tracker(rid: $trackerRid) {
    id
    name
    ticket(id: $ticketId) {
      id
      ref
      status
      resolution
      events(cursor: $cursor) {
        results {
          id
          created
          changes {
            __typename
            eventType
            ticket {
              id
            }
            ... on Created {
              author {
                canonicalName
              }
            }
            ... on Comment {
              author {
                canonicalName
              }
            }
            ... on StatusChange {
              editor {
                canonicalName
              }
              oldStatus
              newStatus
              oldResolution
              newResolution
            }
          }
        }
        cursor
      }
    }
  }
}
""".strip()


TICKET_CLOSED_STATUSES = {"RESOLVED"}
TICKET_CLOSED_RESOLUTIONS = {
    "CLOSED",
    "FIXED",
    "IMPLEMENTED",
    "WONT_FIX",
    "BY_DESIGN",
    "INVALID",
    "DUPLICATE",
    "NOT_OUR_BUG",
}


class TodoSchemaError(RuntimeError):
    """Raised when SourceHut returns an unexpected todo event shape."""


def _safe_nested_name(entity: dict[str, Any] | None) -> str | None:
    if not entity:
        return None
    return entity.get("canonicalName") or entity.get("name")


def _repo_name_from_event(event: dict[str, Any]) -> str | None:
    ticket = event.get("ticket") or {}
    tracker = ticket.get("tracker") or {}
    return tracker.get("name")


def _resource_id_from_event(event: dict[str, Any]) -> str:
    ticket = event.get("ticket") or {}
    return str(ticket.get("ref") or ticket.get("id") or event["id"])


def _change_ticket_id(change: dict[str, Any], event: dict[str, Any]) -> str:
    ticket = change.get("ticket") or event.get("ticket") or {}
    return str(ticket.get("id") or event["id"])


def _normalize_event_change(
    *,
    settings: Settings,
    actor: str,
    event: dict[str, Any],
    change: dict[str, Any],
    occurred_at: datetime,
) -> NormalizedEvent | None:
    change_type = change.get("__typename")
    event_id = str(event["id"])
    resource_id = _resource_id_from_event(event)
    repo_name = _repo_name_from_event(event)
    ticket_id = _change_ticket_id(change, event)

    if change_type == "Created" and _safe_nested_name(change.get("author")) == actor:
        return NormalizedEvent(
            service="todo",
            event_type="ticket_created",
            actor=actor,
            repo_name=repo_name,
            resource_id=resource_id,
            external_uid=f"todo:event:{event_id}:created:{ticket_id}",
            occurred_at=occurred_at,
            weight=settings.event_weights["ticket_created"],
            raw_payload_json={"event": event, "change": change},
        )

    if change_type == "Comment" and _safe_nested_name(change.get("author")) == actor:
        return NormalizedEvent(
            service="todo",
            event_type="ticket_comment",
            actor=actor,
            repo_name=repo_name,
            resource_id=resource_id,
            external_uid=f"todo:event:{event_id}:comment:{ticket_id}",
            occurred_at=occurred_at,
            weight=settings.event_weights["ticket_comment"],
            raw_payload_json={"event": event, "change": change},
        )

    if change_type == "StatusChange" and _safe_nested_name(change.get("editor")) == actor:
        new_status = change.get("newStatus")
        new_resolution = change.get("newResolution")
        if new_status in TICKET_CLOSED_STATUSES or new_resolution in TICKET_CLOSED_RESOLUTIONS:
            return NormalizedEvent(
                service="todo",
                event_type="ticket_closed",
                actor=actor,
                repo_name=repo_name,
                resource_id=resource_id,
                external_uid=f"todo:event:{event_id}:closed:{ticket_id}",
                occurred_at=occurred_at,
                weight=settings.event_weights["ticket_closed"],
                raw_payload_json={"event": event, "change": change},
            )

    return None


def _extract_event_cursor_page(data: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]], str]:
    me = data.get("me") or {}
    canonical_actor = me.get("canonicalName")
    if not canonical_actor:
        raise TodoSchemaError("todo.sr.ht response did not include me.canonicalName")

    events = data.get("events") or {}
    results = events.get("results") or []
    if not isinstance(results, list):
        raise TodoSchemaError("todo.sr.ht response did not include events.results")

    return events.get("cursor"), [event for event in results if isinstance(event, dict)], canonical_actor


@dataclass(slots=True)
class TodoPollResult:
    events: list[NormalizedEvent]
    cursor: str


class TodoIngestionService:
    """Fetches todo.sr.ht activity from the authenticated event feed and normalizes it."""

    service_name = "todo"

    def __init__(self, client: SourceHutGraphQLClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def fetch_recent_events(self, actor: str, since: datetime | None = None) -> TodoPollResult:
        since_dt = ensure_utc(since or (datetime.now(tz=UTC) - timedelta(days=30)))
        cursor_time = datetime.now(tz=UTC).isoformat()
        feed_result = self._fetch_from_activity_feed(actor=actor, since=since_dt)
        if feed_result.events:
            return TodoPollResult(events=feed_result.events, cursor=cursor_time)

        logger.info(
            "todo activity feed returned no normalized events for actor=%s; falling back to tracker crawl",
            actor,
        )
        tracker_events = self._fetch_from_trackers(actor=actor, since=since_dt)
        return TodoPollResult(events=tracker_events, cursor=cursor_time)

    def _fetch_from_activity_feed(self, actor: str, since: datetime) -> TodoPollResult:
        events: list[NormalizedEvent] = []
        cursor: str | None = None
        effective_actor = actor

        for _ in range(10):
            data = self.client.execute(TODO_ACTIVITY_QUERY, {"cursor": cursor})
            cursor, page_events, canonical_actor = _extract_event_cursor_page(data)
            effective_actor = actor or canonical_actor
            logger.info(
                "todo page fetched for actor=%s canonical_actor=%s events=%s next_cursor=%s since=%s",
                actor,
                canonical_actor,
                len(page_events),
                bool(cursor),
                since.isoformat(),
            )

            stop_paging = False
            for event in page_events:
                occurred_at = parse_datetime(event["created"])
                event_id = str(event.get("id"))
                resource_id = _resource_id_from_event(event)
                repo_name = _repo_name_from_event(event)
                change_list = event.get("changes") or []
                logger.info(
                    "todo event id=%s resource=%s repo=%s occurred_at=%s changes=%s",
                    event_id,
                    resource_id,
                    repo_name,
                    occurred_at.isoformat(),
                    len(change_list) if isinstance(change_list, list) else "unknown",
                )
                if occurred_at < since:
                    stop_paging = True
                    logger.info(
                        "todo event id=%s skipped because occurred_at=%s is before since=%s",
                        event_id,
                        occurred_at.isoformat(),
                        since.isoformat(),
                    )
                    continue

                for change in change_list:
                    if not isinstance(change, dict):
                        logger.info("todo event id=%s skipped non-dict change payload", event_id)
                        continue
                    change_type = change.get("__typename")
                    author = _safe_nested_name(change.get("author"))
                    editor = _safe_nested_name(change.get("editor"))
                    logger.info(
                        "todo change event_id=%s type=%s eventType=%s author=%s editor=%s newStatus=%s newResolution=%s",
                        event_id,
                        change_type,
                        change.get("eventType"),
                        author,
                        editor,
                        change.get("newStatus"),
                        change.get("newResolution"),
                    )
                    normalized = _normalize_event_change(
                        settings=self.settings,
                        actor=effective_actor,
                        event=event,
                        change=change,
                        occurred_at=occurred_at,
                    )
                    if normalized is not None:
                        logger.info(
                            "todo change accepted event_id=%s normalized_type=%s external_uid=%s",
                            event_id,
                            normalized.event_type,
                            normalized.external_uid,
                        )
                        events.append(normalized)
                    else:
                        logger.info(
                            "todo change skipped event_id=%s for actor=%s",
                            event_id,
                            effective_actor,
                        )

            if stop_paging or not cursor:
                break

        logger.info("todo poll complete for actor=%s normalized_events=%s", effective_actor, len(events))
        return TodoPollResult(events=events, cursor=datetime.now(tz=UTC).isoformat())

    def _fetch_from_trackers(self, actor: str, since: datetime) -> list[NormalizedEvent]:
        data = self.client.execute(TODO_TRACKERS_QUERY, {"cursor": None})
        me = data.get("me") or {}
        canonical_actor = me.get("canonicalName") or actor
        trackers = ((me.get("trackers") or {}).get("results")) or []
        logger.info("todo tracker crawl actor=%s trackers=%s", canonical_actor, len(trackers))

        events: list[NormalizedEvent] = []
        for tracker in trackers:
            if not isinstance(tracker, dict):
                continue
            tracker_id = tracker.get("id")
            tracker_rid = tracker.get("rid")
            tracker_name = tracker.get("name")
            tracker_events = self._fetch_tracker_tickets(
                actor=canonical_actor,
                tracker_id=str(tracker_id),
                tracker_rid=str(tracker_rid),
                tracker_name=tracker_name,
                since=since,
            )
            events.extend(tracker_events)

        logger.info("todo tracker crawl complete actor=%s normalized_events=%s", canonical_actor, len(events))
        return events

    def _fetch_tracker_tickets(
        self,
        *,
        actor: str,
        tracker_id: str,
        tracker_rid: str,
        tracker_name: str | None,
        since: datetime,
    ) -> list[NormalizedEvent]:
        events: list[NormalizedEvent] = []
        cursor: str | None = None

        for _ in range(50):
            data = self.client.execute(
                TODO_TRACKER_TICKETS_QUERY,
                {"trackerRid": tracker_rid, "cursor": cursor},
            )
            tracker = data.get("tracker") or {}
            tickets_page = tracker.get("tickets") or {}
            tickets = tickets_page.get("results") or []
            cursor = tickets_page.get("cursor")
            logger.info(
                "todo tracker=%s ticket page count=%s next_cursor=%s",
                tracker_name or tracker_id,
                len(tickets),
                bool(cursor),
            )

            stop_paging = False
            for ticket in tickets:
                if not isinstance(ticket, dict):
                    continue
                updated_at = parse_datetime(ticket["updated"])
                if updated_at < since:
                    stop_paging = True
                    logger.info(
                        "todo ticket ref=%s skipped because updated_at=%s is before since=%s",
                        ticket.get("ref"),
                        updated_at.isoformat(),
                        since.isoformat(),
                    )
                    continue
                events.extend(
                    self._fetch_ticket_events(
                        actor=actor,
                        tracker_id=tracker_id,
                        tracker_rid=tracker_rid,
                        tracker_name=tracker_name,
                        ticket=ticket,
                        since=since,
                    )
                )

            if stop_paging or not cursor:
                break

        return events

    def _fetch_ticket_events(
        self,
        *,
        actor: str,
        tracker_id: str,
        tracker_rid: str,
        tracker_name: str | None,
        ticket: dict[str, Any],
        since: datetime,
    ) -> list[NormalizedEvent]:
        events: list[NormalizedEvent] = []
        cursor: str | None = None
        ticket_id = int(ticket["id"])
        ticket_ref = str(ticket.get("ref") or ticket_id)

        for _ in range(50):
            data = self.client.execute(
                TODO_TICKET_EVENTS_QUERY,
                {"trackerRid": tracker_rid, "ticketId": ticket_id, "cursor": cursor},
            )
            tracker = data.get("tracker") or {}
            ticket_payload = (tracker.get("ticket") or {}) if isinstance(tracker, dict) else {}
            event_page = ticket_payload.get("events") or {}
            page_events = event_page.get("results") or []
            cursor = event_page.get("cursor")
            logger.info(
                "todo ticket events ref=%s tracker=%s count=%s next_cursor=%s",
                ticket_ref,
                tracker_name or tracker_id,
                len(page_events),
                bool(cursor),
            )

            stop_paging = False
            for event in page_events:
                if not isinstance(event, dict):
                    continue
                event["ticket"] = {
                    "id": ticket_payload.get("id", ticket.get("id")),
                    "ref": ticket_payload.get("ref", ticket_ref),
                    "status": ticket_payload.get("status", ticket.get("status")),
                    "resolution": ticket_payload.get("resolution", ticket.get("resolution")),
                    "tracker": {"name": tracker_name},
                }
                occurred_at = parse_datetime(event["created"])
                event_id = str(event.get("id"))
                change_list = event.get("changes") or []
                logger.info(
                    "todo ticket event ref=%s id=%s occurred_at=%s changes=%s",
                    ticket_ref,
                    event_id,
                    occurred_at.isoformat(),
                    len(change_list) if isinstance(change_list, list) else "unknown",
                )
                if occurred_at < since:
                    stop_paging = True
                    continue

                for change in change_list:
                    if not isinstance(change, dict):
                        continue
                    logger.info(
                        "todo ticket change ref=%s event_id=%s type=%s eventType=%s author=%s editor=%s newStatus=%s newResolution=%s",
                        ticket_ref,
                        event_id,
                        change.get("__typename"),
                        change.get("eventType"),
                        _safe_nested_name(change.get("author")),
                        _safe_nested_name(change.get("editor")),
                        change.get("newStatus"),
                        change.get("newResolution"),
                    )
                    normalized = _normalize_event_change(
                        settings=self.settings,
                        actor=actor,
                        event=event,
                        change=change,
                        occurred_at=occurred_at,
                    )
                    if normalized is not None:
                        logger.info(
                            "todo ticket change accepted ref=%s event_id=%s normalized_type=%s",
                            ticket_ref,
                            event_id,
                            normalized.event_type,
                        )
                        events.append(normalized)

            if stop_paging or not cursor:
                break

        return events
