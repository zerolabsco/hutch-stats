# API Reference

`srht-contrib` exposes a small HTTP JSON API for health checks, contribution calendar reads, manual polling, and optional tracked repository management.

Base URL examples:

- Local development: `http://127.0.0.1:8000`
- Deployed example: `https://hutch-stats.example.com`

Content type:

- Request bodies: `application/json`
- Response bodies: `application/json`

Authentication:

- Public endpoints:
  - `GET /health`
  - `GET /api/contributions/{actor}`
  - `GET /api/contributions/{actor}/stats`
- Protected endpoints require `X-API-Key`:
  - `POST /api/contributions/poll`
  - all `/api/repositories*`

Example protected header:

```http
X-API-Key: your-api-key
```

## Common Conventions

Actors:

- Actors are SourceHut canonical names such as `~your-user`.
- Actor aliases may resolve to the canonical actor through configured alias mappings.

Dates:

- Query date format is `YYYY-MM-DD`.
- `year` and `from`/`to` are mutually exclusive on contribution endpoints.

Contribution ranges:

- Contribution read endpoints return zero-filled days, so clients do not need to patch missing dates.
- Public contribution reads also register the actor for background indexing. A first lookup may therefore return an empty graph while the scheduler catches up.
- Clients may add `prioritize_self=true` on contribution read endpoints to explicitly request temporary indexing priority for the signed-in user's own graph.
- Incremental indexing and one-year backfill are separate. An actor can be recently indexed before the retained one-year window is fully filled in.
- The service only retains and backfills the most recent 365 days of activity.

Background polling:

- When `ENABLE_SCHEDULER=true`, the service runs one poll immediately at startup and then continues polling on `POLL_INTERVAL_SECONDS`.
- The scheduler always seeds `DEFAULT_ACTOR` as a known actor.
- Public contribution reads register additional actors for later background polling.
- The scheduler only processes due actors, up to `DISCOVERY_BATCH_SIZE` per pass.
- Scheduled first indexing skips bounded one-year backfill work, then drains backfill in later scheduled passes so newly requested actors become indexed sooner.
- Manual polling remains available through `POST /api/contributions/poll`.

Repository names:

- Repository create/update accepts either:
  - shorthand `repo-name`
  - canonical `~owner/repo-name`
- Stored repository names are normalized to canonical `~owner/repo-name` form.
- Git polling auto-discovers repositories owned by the actor through SourceHut.
- Tracked repositories are optional force-includes for git polling; they are not required for normal owned-repository discovery.

## Health

### `GET /health`

Returns a basic service health response.

Auth:

- Public

Response `200 OK`:

```json
{
  "status": "ok"
}
```

## Contributions

### `GET /api/contributions/{actor}`

Returns a contribution calendar for an actor over a year or explicit date range.

Auth:

- Public

Path parameters:

- `actor` string: SourceHut actor, for example `~your-user`

Query parameters:

- `year` integer, optional
- `from` string `YYYY-MM-DD`, optional
- `to` string `YYYY-MM-DD`, optional
- `prioritize_self` boolean, optional

Rules:

- Provide either `year`
- Or provide both `from` and `to`
- Do not combine `year` with `from`/`to`

Behavior notes:

- This endpoint resolves aliases to a canonical actor before querying data.
- This endpoint also registers the actor for background indexing and updates the actor's `last_requested_at` timestamp.
- When `prioritize_self=true`, registration also applies a temporary scheduler boost so that due polls for that actor run ahead of the normal due queue.
- The response is always immediate; it does not wait for SourceHut polling to finish.
- One-year backfill runs in bounded background batches and may take multiple scheduler passes to complete.

Example by year:

```bash
curl "http://127.0.0.1:8000/api/contributions/~your-user?year=2026"
```

Example by range:

```bash
curl "http://127.0.0.1:8000/api/contributions/~your-user?from=2026-03-01&to=2026-04-15"
```

Response `200 OK`:

```json
{
  "actor": "~your-user",
  "from": "2026-03-01",
  "to": "2026-04-15",
  "is_indexed": false,
  "last_polled_at": null,
  "indexing_state": "pending",
  "is_recent_window_backfilled": false,
  "recent_backfill_state": "in_progress",
  "recent_backfill_completed_at": null,
  "days": [
    { "date": "2026-03-01", "count": 0, "score": 0.0 },
    { "date": "2026-03-02", "count": 3, "score": 2.5 }
  ]
}
```

Response fields:

- `actor` string: canonical actor after alias resolution
- `from` string: inclusive start date
- `to` string: inclusive end date
- `is_indexed` boolean: whether the service has already completed at least one successful recent/incremental poll for this actor
- `last_polled_at` string or `null`: most recent successful poll time, if any
- `indexing_state` string: one of `pending`, `indexed`, or `error`
- `is_recent_window_backfilled` boolean: whether the service has finished filling the retained one-year history window
- `recent_backfill_state` string: one of `pending`, `in_progress`, `completed`, or `error`
- `recent_backfill_completed_at` string or `null`: when one-year backfill completed, if it has
- `days` array:
  - `date` string `YYYY-MM-DD`
  - `count` integer contribution count for the day
  - `score` float weighted score for the day

Indexing state semantics:

- `pending`: the actor is known but has not completed a successful poll yet
- `indexed`: at least one successful poll has completed for the actor
- `error`: the most recent poll attempt for the actor failed

Recent backfill semantics:

- the service only retains and backfills the most recent 365 days of activity
- `pending`: the actor has not started one-year backfill yet
- `in_progress`: one-year backfill is actively progressing in bounded background batches
- `completed`: the retained one-year window is fully backfilled
- `error`: the most recent backfill attempt failed

Retention notes:

- activity older than 365 days is not retained
- scheduled polling periodically prunes contribution rows older than the retained window

Possible errors:

- `400 Bad Request` for invalid or conflicting date input

Example `400`:

```json
{
  "detail": "Provide `year` or both `from` and `to`."
}
```

### `GET /api/contributions/{actor}/stats`

Returns aggregated stats for the same date selection rules as the calendar endpoint.

Auth:

- Public

Path parameters:

- `actor` string

Query parameters:

- `year` integer, optional
- `from` string `YYYY-MM-DD`, optional
- `to` string `YYYY-MM-DD`, optional
- `prioritize_self` boolean, optional

Behavior notes:

- This endpoint has the same actor-registration and alias-resolution behavior as the calendar endpoint.
- When `prioritize_self=true`, registration also applies the same temporary scheduler boost as the calendar endpoint.
- This endpoint returns immediately and does not block on SourceHut polling.
- This endpoint also reflects whether the retained one-year history window has been fully backfilled yet.

Example:

```bash
curl "http://127.0.0.1:8000/api/contributions/~your-user/stats?from=2026-03-01&to=2026-04-15"
```

Response `200 OK`:

```json
{
  "actor": "~your-user",
  "from": "2026-03-01",
  "to": "2026-04-15",
  "is_indexed": true,
  "last_polled_at": "2026-04-11T18:05:00Z",
  "indexing_state": "indexed",
  "is_recent_window_backfilled": true,
  "recent_backfill_state": "completed",
  "recent_backfill_completed_at": "2026-04-11T18:02:00Z",
  "total_events": 126,
  "total_score": 116.75,
  "active_days": 14,
  "longest_streak": 5,
  "current_streak": 0
}
```

Response fields:

- `actor` string
- `from` string
- `to` string
- `is_indexed` boolean
- `last_polled_at` string or `null`
- `indexing_state` string
- `is_recent_window_backfilled` boolean
- `recent_backfill_state` string
- `recent_backfill_completed_at` string or `null`
- `total_events` integer
- `total_score` float
- `active_days` integer
- `longest_streak` integer
- `current_streak` integer

Possible errors:

- `400 Bad Request` for invalid or conflicting date input

### `POST /api/contributions/poll`

Triggers a manual SourceHut poll for the given actor and stores any newly discovered events.

Auth:

- Requires `X-API-Key`

Query parameters:

- `actor` string: SourceHut actor to poll

Example:

```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  "http://127.0.0.1:8000/api/contributions/poll?actor=~your-user"
```

Response `200 OK`:

```json
{
  "actor": "~your-user",
  "inserted_events": 57,
  "services": ["todo", "git"]
}
```

Response fields:

- `actor` string: canonical actor after alias resolution
- `inserted_events` integer: number of newly inserted normalized events
- `services` array of strings: currently `["todo", "git"]`

Behavior notes:

- Manual polling also updates the actor's indexing metadata.
- Manual polling also advances one-year backfill by bounded batches for each supported service.
- Git polling auto-discovers the actor's owned repositories and unions in any configured tracked repositories.

Possible errors:

- `401 Unauthorized` if the API key is missing or invalid
- `502 Bad Gateway` if polling SourceHut fails

Example `401`:

```json
{
  "detail": "Invalid API key."
}
```

Example `502`:

```json
{
  "detail": "SourceHut polling failed: HTTP error from SourceHut: 502"
}
```

## Tracked Repositories

All repository endpoints are protected and require `X-API-Key`.

Tracked repositories are optional force-includes for git polling. Each repository is associated with an actor and stored in canonical `~owner/repo` form.

### `GET /api/repositories`

Lists tracked git repositories.

Auth:

- Requires `X-API-Key`

Query parameters:

- `actor` string, optional: filter to a canonical actor or alias

Example:

```bash
curl \
  -H "X-API-Key: your-api-key" \
  "http://127.0.0.1:8000/api/repositories?actor=~your-user"
```

Response `200 OK`:

```json
[
  {
    "id": 1,
    "service": "git",
    "actor": "~your-user",
    "repo_name": "~your-user/your-repo"
  }
]
```

### `GET /api/repositories/{repository_id}`

Fetches one tracked repository by numeric ID.

Auth:

- Requires `X-API-Key`

Path parameters:

- `repository_id` integer

Example:

```bash
curl \
  -H "X-API-Key: your-api-key" \
  "http://127.0.0.1:8000/api/repositories/1"
```

Response `200 OK`:

```json
{
  "id": 1,
  "service": "git",
  "actor": "~your-user",
  "repo_name": "~your-user/your-repo"
}
```

Possible errors:

- `404 Not Found` if the repository ID does not exist

### `POST /api/repositories`

Creates a tracked repository entry.

Auth:

- Requires `X-API-Key`

Request body:

```json
{
  "actor": "~your-user",
  "repo_name": "your-repo"
}
```

Example:

```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"actor":"~your-user","repo_name":"your-repo"}' \
  "http://127.0.0.1:8000/api/repositories"
```

Response `201 Created`:

```json
{
  "id": 1,
  "service": "git",
  "actor": "~your-user",
  "repo_name": "~your-user/your-repo"
}
```

Possible errors:

- `401 Unauthorized` if the API key is missing or invalid
- `409 Conflict` if the normalized repository already exists for that actor
- `422 Unprocessable Content` if `actor` or `repo_name` is blank or malformed

### `PATCH /api/repositories/{repository_id}`

Updates an existing tracked repository.

Auth:

- Requires `X-API-Key`

Path parameters:

- `repository_id` integer

Request body:

```json
{
  "actor": "~your-user",
  "repo_name": "~your-user/your-other-repo"
}
```

Body rules:

- At least one of `actor` or `repo_name` must be present

Example:

```bash
curl -X PATCH \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"repo_name":"~your-user/your-other-repo"}' \
  "http://127.0.0.1:8000/api/repositories/1"
```

Response `200 OK`:

```json
{
  "id": 1,
  "service": "git",
  "actor": "~your-user",
  "repo_name": "~your-user/your-other-repo"
}
```

Possible errors:

- `404 Not Found`
- `409 Conflict`
- `422 Unprocessable Content`

### `DELETE /api/repositories/{repository_id}`

Deletes a tracked repository.

Auth:

- Requires `X-API-Key`

Path parameters:

- `repository_id` integer

Example:

```bash
curl -X DELETE \
  -H "X-API-Key: your-api-key" \
  "http://127.0.0.1:8000/api/repositories/1"
```

Response `204 No Content`

Possible errors:

- `404 Not Found`

## Error Summary

Common status codes:

- `200 OK` successful read or manual poll
- `201 Created` successful repository creation
- `204 No Content` successful repository deletion
- `400 Bad Request` invalid date parameters
- `401 Unauthorized` missing or invalid API key
- `404 Not Found` missing repository record
- `409 Conflict` duplicate repository after normalization
- `422 Unprocessable Content` invalid repository payload
- `502 Bad Gateway` upstream SourceHut failure during poll

## OpenAPI

FastAPI also serves an OpenAPI document at:

```text
/openapi.json
```

If interactive docs are enabled by your deployment, the standard FastAPI docs may also be available at:

```text
/docs
```
