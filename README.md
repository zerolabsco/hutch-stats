# srht-contrib

`srht-contrib` is a small Python service that polls SourceHut activity, normalizes it into one internal event model, stores it in SQLite, and exposes a contribution-calendar JSON API that an iOS app can render directly.

The current V1 is intentionally narrow and production-oriented:

- FastAPI JSON API only
- SQLite-backed persistence
- polling-based ingestion
- complete `todo.sr.ht` ingestion path
- practical `git.sr.ht` commit ingestion with automatic repository discovery
- public read-only contribution endpoints plus API-key protection for mutating/admin routes
- Alembic-managed schema migrations

## What It Does

The service collects SourceHut activity from one or more sr.ht GraphQL services, turns those records into a canonical event shape, aggregates activity by day, and returns zero-filled calendar ranges so the client never has to patch missing dates.

Example use cases:

- render a GitHub-style contribution grid in an iOS app
- show total score and streak stats for a SourceHut user
- poll recent activity on a schedule or trigger polling manually

## Architecture Overview

The code is split into small, testable layers:

- `src/srht_contrib/config.py`: environment-driven settings and event weights
- `src/srht_contrib/db.py`: SQLAlchemy engine/session setup and app-scoped DB access
- `src/srht_contrib/models.py`: ORM models for normalized events, sync state, aliases, and tracked repos
- `src/srht_contrib/services/srht_client.py`: generic SourceHut GraphQL client with error handling and simple retries
- `src/srht_contrib/services/todo.py`: `todo.sr.ht` ingestion and normalization
- `src/srht_contrib/services/git.py`: `git.sr.ht` tracked-repository commit ingestion
- `src/srht_contrib/services/aggregator.py`: per-day aggregation and streak/stat calculations
- `src/srht_contrib/jobs/poller.py`: repeated-safe polling and idempotent persistence
- `src/srht_contrib/api/`: FastAPI routes, auth dependencies, and repository management
- `alembic/`: schema migration environment and versioned migrations

## Supported sr.ht Services

### Implemented

- `todo.sr.ht`
- `git.sr.ht`

Current normalized event types:

- `ticket_created`
- `ticket_comment`
- `ticket_closed`
- `commit`

`todo.sr.ht` uses a feed-first strategy and falls back to crawling the authenticated user’s trackers, tickets, and ticket events when the top-level activity feed is empty. `git.sr.ht` polls the actor's owned repositories for recent commits on the default branch and unions in any explicitly configured repositories.

## Canonical Event Model

All ingestion services normalize external activity into this shape:

- `service`
- `event_type`
- `actor`
- `repo_name`
- `resource_id`
- `external_uid`
- `occurred_at`
- `weight`
- `raw_payload_json`

The database enforces uniqueness on `(service, external_uid)` so polling is safe to repeat.

Tracked git repositories are persisted in the `tracked_repositories` table and stored in canonical `~owner/repo` form. They are optional overrides now: the poller auto-discovers an actor's owned repositories and unions in any configured or API-managed repositories.

## Configuration

Environment variables:

- `API_KEY`: required header token for mutating/admin routes via `X-API-Key`
- `ENABLE_SCHEDULER`: defaults to `false`; enables in-process polling when set to `true`
- `SRHT_TOKEN`: bearer token for SourceHut GraphQL
- `TODO_SRHT_ENDPOINT`: defaults to `https://todo.sr.ht/query`
- `GIT_SRHT_ENDPOINT`: defaults to `https://git.sr.ht/query`
- `DATABASE_URL`: defaults to `sqlite:///./srht_contrib.db`
- `DEFAULT_ACTOR`: actor used by the scheduled poll job
- `POLL_INTERVAL_SECONDS`: scheduler interval in seconds
- `ACTOR_ALIASES_JSON`: optional JSON object for actor/email/display-name alias mapping
- `GIT_TRACKED_REPOSITORIES`: optional JSON array of repository names or `owner/repo` strings to union into git polling

Example `.env`:

```env
API_KEY=replace-me
ENABLE_SCHEDULER=false
SRHT_TOKEN=replace-me
TODO_SRHT_ENDPOINT=https://todo.sr.ht/query
GIT_SRHT_ENDPOINT=https://git.sr.ht/query
DATABASE_URL=sqlite:///./srht_contrib.db
DEFAULT_ACTOR=~your-user
POLL_INTERVAL_SECONDS=900
ACTOR_ALIASES_JSON={"~your-user":["you@example.com","Your Name"]}
GIT_TRACKED_REPOSITORIES=["your-repo","~your-user/your-site"]
```

## Local Run Instructions

### 1. Create a virtual environment and install dependencies

Using `uv`:

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Using `pip`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
```

Set at least:

- `API_KEY`
- `SRHT_TOKEN`
- `DEFAULT_ACTOR`
- `GIT_TRACKED_REPOSITORIES` if you want to force-include extra repositories beyond the actor's owned repos

### 3. Run database migrations

```bash
alembic upgrade head
```

### 4. Run the API

```bash
uvicorn srht_contrib.main:app --reload
```

## Manual Polling

Manual polling is exposed as an API endpoint:

```bash
curl -X POST "http://127.0.0.1:8000/api/contributions/poll?actor=~your-user" \
  -H "X-API-Key: replace-me"
```

Example response:

```json
{
  "actor": "~your-user",
  "inserted_events": 3,
  "services": ["todo", "git"]
}
```

Scheduled polling only runs when `ENABLE_SCHEDULER=true`. The scheduler seeds `DEFAULT_ACTOR` as an initial known actor, and public contribution reads register additional actors for later background polling.

For `git.sr.ht`, owned repositories are auto-discovered for the actor. `GIT_TRACKED_REPOSITORIES` can still be used to union in extra repositories. Entries may be either:

- `"Hutch"` for a repository owned by `DEFAULT_ACTOR`
- `"~your-user/your-site"` for an explicit owner/repository pair

## API Endpoints

### Health

```bash
curl "http://127.0.0.1:8000/health"
```

Response:

```json
{"status":"ok"}
```

### Contribution Calendar by Year

```bash
curl "http://127.0.0.1:8000/api/contributions/~your-user?year=2026"
```

### Contribution Calendar by Date Range

```bash
curl "http://127.0.0.1:8000/api/contributions/~your-user?from=2026-01-01&to=2026-03-30"
```

Example response:

```json
{
  "actor": "~your-user",
  "from": "2026-01-01",
  "to": "2026-03-30",
  "is_indexed": true,
  "last_polled_at": "2026-04-11T18:05:00Z",
  "indexing_state": "indexed",
  "days": [
    {"date": "2026-03-28", "count": 3, "score": 3.5},
    {"date": "2026-03-29", "count": 0, "score": 0.0},
    {"date": "2026-03-30", "count": 7, "score": 8.25}
  ]
}
```

### Contribution Stats

```bash
curl "http://127.0.0.1:8000/api/contributions/~your-user/stats?year=2026"
```

Example response:

```json
{
  "actor": "~your-user",
  "from": "2026-01-01",
  "to": "2026-12-31",
  "total_events": 42,
  "total_score": 37.5,
  "active_days": 18,
  "longest_streak": 5,
  "current_streak": 2
}
```

### Tracked Repositories

List tracked repositories:

```bash
curl "http://127.0.0.1:8000/api/repositories?actor=~your-user" \
  -H "X-API-Key: replace-me"
```

Create a tracked repository:

```bash
curl -X POST "http://127.0.0.1:8000/api/repositories" \
  -H "X-API-Key: replace-me" \
  -H "Content-Type: application/json" \
  -d '{"actor":"~your-user","repo_name":"your-repo"}'
```

Get, update, and delete a tracked repository:

```bash
curl "http://127.0.0.1:8000/api/repositories/1" \
  -H "X-API-Key: replace-me"

curl -X PATCH "http://127.0.0.1:8000/api/repositories/1" \
  -H "X-API-Key: replace-me" \
  -H "Content-Type: application/json" \
  -d '{"repo_name":"~your-user/your-site"}'

curl -X DELETE "http://127.0.0.1:8000/api/repositories/1" \
  -H "X-API-Key: replace-me"
```

## Event Weighting

Weights live in `src/srht_contrib/config.py` so they are easy to tune without touching aggregation code:

- `commit`: `1.0`
- `ticket_created`: `1.0`
- `ticket_comment`: `0.5`
- `ticket_closed`: `0.75`
- `build_started`: `0.25`
- `build_passed`: `0.25`

## Testing

Run the test suite with:

```bash
pytest
```

Covered areas:

- health endpoint
- public read-only contribution endpoints
- API key enforcement for mutating/admin routes
- calendar aggregation
- zero-filled ranges
- stats calculations
- invalid date handling
- idempotent ingestion
- todo feed fallback traversal
- repository CRUD and normalization
- SourceHut error mapping
- git commit alias normalization
- Alembic upgrade path

## SourceHut Schema Assumptions

The SourceHut-specific assumptions are isolated to the service modules:

- `src/srht_contrib/services/todo.py` uses the authenticated `events(cursor)` feed first, then falls back to tracker/ticket event traversal for reliable contribution discovery.
- `src/srht_contrib/services/git.py` discovers owned repositories for an actor, polls each repository `log(cursor)`, and attributes commits through the configured alias map.

## Known Limitations

- `git.sr.ht` polling assumes the actor's repositories are discoverable through the SourceHut GraphQL API
- scheduled polling runs in-process, so it is not a distributed scheduler
- newly requested actors are indexed asynchronously, so the first public read may be empty until a scheduler or manual poll runs
- alias management is config-driven; there is no alias CRUD API yet
- current deployment model is trusted-operator V1, not a public multi-tenant service

## Deployment Notes

- Add a `.dockerignore` when building container images so local secrets and SQLite files are never sent to the build context.
- For production, prefer exposing the service behind a reverse proxy instead of publishing the application port directly to the internet.
- Set `ENABLE_SCHEDULER=true` only for single-instance deployments where this service should own polling.

## Recommended Next Steps

1. Add alias-management APIs or seed files for stronger actor identity mapping.
2. Add more SourceHut services such as `builds.sr.ht` and `lists.sr.ht`.
3. Move scheduled polling into an external worker if the deployment grows past a single process.
